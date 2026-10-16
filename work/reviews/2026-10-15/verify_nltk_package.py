"""Verify the distributable, without project checkout imports or site packages."""
import hashlib
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path(__file__).parent / "evidence"
wheel = next((EVIDENCE / "nltk-wheel").glob("*.whl"))
with zipfile.ZipFile(wheel) as archive:
    required = ["manifest.json", "LICENSE.txt", "NOTICE.md", "PUNKT_README.txt",
                "abbrev_types.txt", "collocations.tab", "ortho_context.tab", "sent_starters.txt"]
    assert all("backend/_vendor/cmrc_tokenizer/" + name in archive.namelist() for name in required)
    metadata = archive.read("saas_implementation_agent-0.1.0.dist-info/METADATA").decode()
    assert not any(line.lower().startswith("requires-dist: nltk") for line in metadata.splitlines())

child = """
import json,sys,importlib.util
sys.path.insert(0,sys.argv[1])
assert importlib.util.find_spec('nltk') is None
from backend.benchmark_tokenizer import word_tokenize
from backend.cmrc_evaluation import answer_scores,score_run
assert '.whl' in sys.modules['backend.benchmark_tokenizer'].__file__
assert word_tokenize("Hello world. Next!") == ['Hello','world','.','Next','!']
assert answer_scores(["北京 can't stop. Next!"], "北京 can't stop. Next!") == {'em':1.0,'f1':1.0}
corpus,runs=sys.argv[2:]
report=score_run(corpus,json.load(open(runs,encoding='utf-8')))
print(json.dumps({'nltk_installed':False,'zip_resource_load':True,'metrics':report['metrics']}))
"""
result = subprocess.run([sys.executable, "-I", "-S", "-c", child, str(wheel),
    str(ROOT / "data/evaluations/cmrc2018-200"),
    str(ROOT / "data/evaluations/cmrc-200-final-20261012/run.json")],
    cwd=EVIDENCE, check=True, text=True, capture_output=True, timeout=60)
verification = json.loads(result.stdout)
verification["wheel_sha256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
verification["bundled_resources"] = required

# Establish that extracted reference source really came from the published wheel.
client = httpx.Client(timeout=60, follow_redirects=True)
pypi = client.get("https://pypi.org/pypi/nltk/3.10.3/json").raise_for_status().json()
upstream = next(item for item in pypi["urls"] if item["filename"].endswith(".whl"))
raw = client.get(upstream["url"]).raise_for_status().content
assert hashlib.sha256(raw).hexdigest() == upstream["digests"]["sha256"]
source_manifest = json.loads((ROOT / "backend/_vendor/cmrc_tokenizer/manifest.json").read_bytes())
with zipfile.ZipFile(io.BytesIO(raw)) as archive:
    for name, expected in source_manifest["upstream_source_sha256"].items():
        assert hashlib.sha256(archive.read("nltk/tokenize/" + name)).hexdigest() == expected
verification["reference_wheel_sha256"] = upstream["digests"]["sha256"]
verification["reference_source_verified"] = True
(EVIDENCE / "nltk-package-verification.json").write_text(json.dumps(verification,indent=2),encoding="utf-8")
print(json.dumps(verification))
