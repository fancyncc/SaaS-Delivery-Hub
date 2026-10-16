"""Offline CMRC tokenizer with the historical NLTK 3.10.3 scoring semantics.

Only the English Punkt inference algorithm and improved Treebank word splitting
are retained. Resource names are fixed, packaged, hash checked and never obtained
from NLTK_DATA, the working directory, pickle or the network.
"""

from collections import defaultdict
from functools import lru_cache
from hashlib import sha256
from importlib.resources import files

from backend._vendor.cmrc_tokenizer.destructive import NLTKWordTokenizer
from backend._vendor.cmrc_tokenizer.punkt import PunktParameters, PunktSentenceTokenizer

REFERENCE_NLTK_VERSION = "3.10.3"
MODEL_SHA256 = {
    "abbrev_types.txt": "92a3e070f43d9b4c5534758ca40ad7343b04e7e29bfe0c2eb658a39445a4f779",
    "collocations.tab": "8e2da1225e4dd2cc9dba261ee231ccb134859e21b46006e7f472c5ee269af0cf",
    "ortho_context.tab": "4bbcca25ed3d3f06c02402abf8419b9f033b8adc06e7b482eca4e45f81a5dc4c",
    "sent_starters.txt": "f3f8535483e1dba487241b764945168123bca3209a9645e59acd1225dc76edac",
}


def _resource_text(name: str) -> str:
    # Check membership before reading, even for internal callers.
    expected = MODEL_SHA256[name]
    raw = files("backend._vendor.cmrc_tokenizer").joinpath(name).read_bytes()
    if sha256(raw).hexdigest() != expected:
        raise ValueError(f"CMRC tokenizer resource checksum mismatch: {name}")
    return raw.decode("utf-8")


@lru_cache(maxsize=1)
def _tokenizers() -> tuple[PunktSentenceTokenizer, NLTKWordTokenizer]:
    params = PunktParameters()
    # splitlines() removes newlines exactly as the upstream Punkt tab decoder.
    params.abbrev_types = set(_resource_text("abbrev_types.txt").splitlines())
    params.sent_starters = set(_resource_text("sent_starters.txt").splitlines())
    params.collocations = {tuple(line.split("\t")) for line in _resource_text("collocations.tab").splitlines()}
    params.ortho_context = defaultdict(int, {
        word: int(value)
        for word, value in (line.split("\t") for line in _resource_text("ortho_context.tab").splitlines())
    })
    return PunktSentenceTokenizer(params=params), NLTKWordTokenizer()


def word_tokenize(text: str) -> list[str]:
    sentences, words = _tokenizers()
    return [token for sentence in sentences.tokenize(text) for token in words.tokenize(sentence)]


def tokenizer_metadata() -> dict:
    return {
        "implementation": "bundled_cmrc_punkt_treebank_v1",
        "reference_nltk_version": REFERENCE_NLTK_VERSION,
        "model_files_sha256": dict(MODEL_SHA256),
    }
