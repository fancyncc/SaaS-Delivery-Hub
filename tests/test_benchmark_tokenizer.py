"""Scoring parity and safe offline loading, with no NLTK installation/assets."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend import benchmark_tokenizer as tokenizer
from backend.cmrc_evaluation import answer_scores, mixed_segmentation

REFERENCE = json.loads((Path(__file__).parent / "fixtures/cmrc_tokenizer_reference.json").read_bytes())


@pytest.mark.parametrize("case", REFERENCE["cases"], ids=lambda case: case["text"][:45])
def test_tokens_match_recorded_nltk_reference(case):
    assert tokenizer.word_tokenize(case["text"]) == case["word_tokens"]
    assert mixed_segmentation(case["text"]) == case["mixed_tokens"]


@pytest.mark.parametrize("case", REFERENCE["scores"])
def test_mixed_english_scores_match_recorded_reference(case):
    assert answer_scores(case["answers"], case["prediction"]) == case["score"]


def test_tokenizer_ignores_external_data_and_needs_no_nltk_or_network(tmp_path):
    root = Path(__file__).resolve().parents[1]
    code = """
import importlib.abc
import socket
import sys
sys.path.insert(0, sys.argv[1])
class BlockNLTK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname == 'nltk' or fullname.startswith('nltk.'):
            raise AssertionError('NLTK must not be imported')
sys.meta_path.insert(0, BlockNLTK())
def offline(*args, **kwargs):
    raise AssertionError('Network must not be used')
socket.socket = socket.create_connection = offline
from backend.cmrc_evaluation import answer_scores
assert answer_scores(["北京 can't stop. Next!"], "北京 can't stop. Next!") == {'em': 1.0, 'f1': 1.0}
assert not any(n == 'nltk' or n.startswith('nltk.') for n in sys.modules)
"""
    env = {**os.environ, "NLTK_DATA": str(tmp_path / "untrusted-and-missing")}
    subprocess.run([sys.executable, "-I", "-c", code, str(root)],
                   cwd=tmp_path, env=env, check=True, capture_output=True, text=True, timeout=30)


def test_corrupted_parameters_fail_closed(monkeypatch, tmp_path):
    source = Path(__file__).resolve().parents[1] / "backend/_vendor/cmrc_tokenizer"
    for name in tokenizer.MODEL_SHA256:
        (tmp_path / name).write_bytes((source / name).read_bytes())
    (tmp_path / "ortho_context.tab").write_text("bad\t123\n", encoding="utf-8")
    monkeypatch.setattr(tokenizer, "files", lambda package: tmp_path)
    tokenizer._tokenizers.cache_clear()
    try:
        with pytest.raises(ValueError, match="checksum mismatch: ortho_context.tab"):
            tokenizer.word_tokenize("A. Bach went home. Next!")
    finally:
        tokenizer._tokenizers.cache_clear()


def test_resource_names_cannot_escape_package():
    with pytest.raises(KeyError):
        tokenizer._resource_text("../../../../pyproject.toml")


def test_scoring_metadata_distinguishes_reference_from_installed_dependency():
    metadata = tokenizer.tokenizer_metadata()
    assert metadata["reference_nltk_version"] == REFERENCE["reference_nltk_version"]
    assert metadata["implementation"] == "bundled_cmrc_punkt_treebank_v1"
    assert len(metadata["model_files_sha256"]) == 4
