"""One-time, reviewable extraction of inference-only tokenizer source."""
import ast
import hashlib
import io
import json
from pathlib import Path
import zipfile

import httpx

ROOT = Path(__file__).resolve().parents[3]
DEST = ROOT / "backend/_vendor/cmrc_tokenizer"
SOURCE = ROOT / ".venv/Lib/site-packages/nltk"
MODEL_REV = "550b6625bcef1f2abff2ff770a5a0d272c9c6b2a"
DEST.mkdir(parents=True, exist_ok=False)
(DEST.parent / "__init__.py").write_text('"""Narrowly scoped third-party code; see each component NOTICE."""\n')
(DEST / "__init__.py").write_text('"""CMRC tokenizer inference subset. See NOTICE.md for provenance and changes."""\n')


def extract(name, classes, methods=None):
    raw = (SOURCE / f"tokenize/{name}.py").read_text(encoding="utf-8")
    lines = raw.splitlines(keepends=True)
    pieces = []
    for node in ast.parse(raw).body:
        if isinstance(node, ast.ClassDef) and node.name in classes:
            removed = set()
            if methods and node.name in methods:
                for member in node.body:
                    if isinstance(member, ast.FunctionDef) and member.name not in methods[node.name]:
                        start = min([member.lineno] + [d.lineno for d in member.decorator_list])
                        removed.update(range(start, member.end_lineno + 1))
            segment = ''.join(line for i, line in enumerate(lines, 1)
                              if node.lineno <= i <= node.end_lineno and i not in removed)
            segment = segment.replace('(PunktBaseClass, TokenizerI)', '(PunktBaseClass)')
            segment = segment.replace('(TokenizerI)', '')
            pieces.append(segment)
        elif name == "punkt" and isinstance(node, (ast.Assign, ast.AnnAssign)):
            names = [n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)]
            if names and all(n.startswith('_ORTHO_') or n.startswith('REASON_') or n == '_re_non_punct' for n in names):
                pieces.append(''.join(lines[node.lineno - 1:node.end_lineno]))
        elif name == "punkt" and isinstance(node, ast.FunctionDef) and node.name == '_pair_iter':
            pieces.append(''.join(lines[node.lineno - 1:node.end_lineno]))
    header = raw[:raw.index('\n\n')]
    imports = ('import re\nimport string\nfrom collections import defaultdict\n'
               'from collections.abc import Iterator\nfrom re import Match\n') if name == "punkt" else (
               'import re\nimport warnings\n')
    output = header + '\n# Inference-only subset; modified 2026-10-15. See NOTICE.md.\n\n' + imports + '\n\n' + '\n\n'.join(pieces) + '\n'
    (DEST / f"{name}.py").write_text(output, encoding="utf-8")
    return hashlib.sha256(raw.encode()).hexdigest()


source_hashes = {
    'punkt.py': extract('punkt', {'PunktLanguageVars', 'PunktParameters', 'PunktToken', 'PunktBaseClass', 'PunktSentenceTokenizer'},
        {'PunktLanguageVars': {'_re_sent_end_chars', '_re_non_word_chars', '_word_tokenizer_re', 'word_tokenize', 'period_context_re'},
         'PunktParameters': {'__init__'},
         'PunktSentenceTokenizer': {'tokenize', 'span_tokenize', 'sentences_from_text', '_get_last_whitespace_index',
            '_match_potential_end_contexts', '_slices_from_text', '_realign_boundaries', 'text_contains_sentbreak',
            '_annotate_tokens', '_annotate_second_pass', '_second_pass_annotation', '_ortho_heuristic'}}),
    'destructive.py': extract('destructive', {'MacIntyreContractions', 'NLTKWordTokenizer'},
                             {'NLTKWordTokenizer': {'tokenize'}}),
}
license_file = ROOT / '.venv/Lib/site-packages/nltk-3.10.3.dist-info/licenses/LICENSE.txt'
(DEST / 'LICENSE.txt').write_bytes(license_file.read_bytes())
url = f'https://raw.githubusercontent.com/nltk/nltk_data/{MODEL_REV}/packages/tokenizers/punkt_tab.zip'
response = httpx.get(url, timeout=60, follow_redirects=True)
response.raise_for_status()
archive = zipfile.ZipFile(io.BytesIO(response.content))
model_hashes = {}
for name in ['abbrev_types.txt', 'collocations.tab', 'ortho_context.tab', 'sent_starters.txt']:
    raw = archive.read(f'punkt_tab/english/{name}')
    assert raw == (ROOT / 'data/evaluations/nltk_data/tokenizers/punkt_tab/english' / name).read_bytes()
    (DEST / name).write_bytes(raw)
    model_hashes[name] = hashlib.sha256(raw).hexdigest()
(DEST / 'PUNKT_README.txt').write_bytes(archive.read('punkt_tab/README'))
manifest = {'reference_nltk_version': '3.10.3', 'upstream_source_sha256': source_hashes,
            'model_repository_revision': MODEL_REV, 'model_archive_url': url,
            'model_archive_sha256': hashlib.sha256(response.content).hexdigest(),
            'model_files_sha256': model_hashes}
(DEST / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
print(json.dumps(manifest, indent=2))
