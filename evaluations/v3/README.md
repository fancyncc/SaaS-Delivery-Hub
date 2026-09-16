# V3 evaluation and release

V3 is disabled. `cases.jsonl` contains 120 **synthetic boundary cases**, not 120 independently reviewed labels. The calibration domains (inventory, education, facilities) and locked validation domains (publishing, events, software) have no overlap. Every domain contains all five formats. Human review, diverse long documents, comparison questions, and additional permission fixtures must be completed before this corpus qualifies for release. Do not change `review_status` merely because automated tests pass.

## Run

1. Apply migrations 0022–0023; set `RAG_V3_INDEXING_ENABLED=true` in a staging environment. Upload files via `/api/knowledge/files`, or enqueue existing non-PDF text via `POST /api/knowledge/{id}/v3-index`. The legacy vector index is untouched.
2. Compare the current BGE pair against `BAAI/bge-m3` / `BAAI/bge-reranker-v2-m3` in a **separate model service**. The multilingual model is a candidate, not a selected winner. Set `RAG_V3_MODEL_PROFILE` to a JSON settings file; it overrides only V3 embedding/reranker settings. Pin model revisions to 40-character weight commits. Do not use `main` in a release. V3 vectors support 512 and 1024 dimensions and authorize/filter identity before distance evaluation.
3. Record candidate features and independently reviewed relevant/irrelevant labels as JSONL: `split`, `relevant`, `features` (rerank, gap, identifier, query_terms, heading_terms, structured, phrase, condition_terms). Run `python scripts/evaluate_rag_v3.py calibrate --runs calibration-features.jsonl --output classifier.json`. Only calibration rows are accepted. Freeze classifier and manifest hashes before validation.
4. Record paired runs with fields `id`, `format`, `kind`, `necessary_total`, `recalled_necessary`, `covered_necessary`, `old_covered_necessary`, `old_tokens`, `final_tokens`, `budget`, `returned_relevant`, `permission_leaks`, `source_mismatches`, `fact_corruptions`, `warm_latency_ms`, `old_warm_latency_ms`. Necessary-fact matching requires verified source positions, values and protected conditions, not substring-only keyword checks. Cold starts, parsing and indexing durations are recorded separately.
5. Run `python scripts/evaluate_rag_v3.py validate --cases evaluations/v3/cases.jsonl --runs locked-runs.jsonl --output report.json`. Failed gates remain failures; validation must not be used to tune parameters. Pair the model profiles by Chinese/English macro recall, completeness, and warm latency. Equivalent results favor the current pair.
6. Independently review the report. The release report must contain checked metrics per format, model comparison and reviewer approval, `identity` and SHA256 of canonical classifier JSON. A release file contains `identity`, `classifier`, `report`, and `report_sha256`. The API checks these before retrieval. Only then set `RAG_INSPECTION_V3=true` for inspect. Never modify chat/Agent settings to trial a V3 profile.

## Current limitations / release blockers

- Synthetic corpus is not independently reviewed and is not sufficiently diverse for release.
- Real two-model comparison, locked validation and staging latency measurements are pending.
- Long indivisible atoms are split only for indexing; they are reassembled only when all required parts pass relevance filtering and fit the budget. Otherwise they are omitted with an explicit diagnostic.
- Exact body duplicates are deduplicated within a source; cross-source near-duplicate merging is deliberately conservative. JSON path conflicts are flagged as potential conflicts, not resolved answers.
- Query evidence uses the retrieval tokenizer; future generation tokenizer and full request budgets are outside this release.

Model references: [BGE-M3](https://huggingface.co/BAAI/bge-m3), [BGE reranker v2 M3](https://huggingface.co/BAAI/bge-reranker-v2-m3). Review the exact pinned revisions used by the model service, not only these mutable landing pages.

Rollback: set `RAG_INSPECTION_V3=false`; pause indexing separately with `RAG_V3_INDEXING_ENABLED=false`. Preserve failed reports and side-index data for diagnosis. Existing PDF records remain on the legacy path; new PDF and DOC uploads are rejected.
