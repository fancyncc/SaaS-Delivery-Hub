# CMRC tokenizer inference subset

Copyright (C) 2001–2026 NLTK Project. Apache License 2.0 (see LICENSE.txt).
Original authors and algorithm credits are retained in each source file.

Sources: NLTK **3.10.3**, `nltk/tokenize/punkt.py` and
`nltk/tokenize/destructive.py`, from the published PyPI wheel.
https://github.com/nltk/nltk/tree/3.10.3/nltk/tokenize
https://pypi.org/project/nltk/3.10.3/

Modified on 2026-10-15: retain only English sentence/word tokenization inference;
remove the NLTK interface base classes, training, debugging, span alignment,
serialization, model load/save, pickle, corpus discovery and downloader APIs.
The sentence tokenizer inherits the parameter-based base constructor. Token
splitting rules, sentence boundary heuristics and ReDoS fixes are unchanged.
Unused imports are removed and standard lint fixes applied. This is not a full
NLTK distribution or a claim that NLTK's outstanding advisories are fixed.

The four English Punkt parameter files are copied byte for byte from the NLTK
data repository at revision `550b6625bcef1f2abff2ff770a5a0d272c9c6b2a`.
https://github.com/nltk/nltk_data/tree/550b6625bcef1f2abff2ff770a5a0d272c9c6b2a
That repository distributes its files under Apache License 2.0 (LICENSE.txt).
Model contributors: Jan Strunk / Tibor Kiss. Original model documentation and
training-corpus attribution are retained in PUNKT_README.txt; its training
examples are historical documentation, not supported APIs in this subset.
Full source and resource hashes are recorded in manifest.json.

Only `backend.benchmark_tokenizer` should load the packaged parameters. It
verifies their SHA-256 digests before parsing plain UTF-8 data and accepts no
caller-controlled file paths or language selection. No NLTK_DATA lookup, network
access, arbitrary file write, dynamic code execution or pickle is included.

When updating this subset, review upstream tokenizer security changes, update
provenance and digests, and compare tokens and CMRC scores against the previous
implementation. Do not change the CMRC scoring algorithm or substitute a regex
splitter or sentence-free tokenization.
