# V3 blind holdout v1

This is a one-time generalization check created after the original 192-case development corpus had already been used for pipeline iteration.

- Five newly authored SaaS implementation documents.
- Thirty questions: 25 answerable and five no-answer cases.
- Unseen organizations, identifiers, numbers, wording and document structure.
- Chinese and English questions covering direct facts, enumerations, conditions, exceptions, contrasts and multi-span answers.
- Labels are agent-authored and have not received independent human review.
- The corpus is frozen by SHA-256 before the first run.
- No labels, retrieval rules, model settings or source documents may be changed after viewing the first result. Failures remain recorded.

This dataset measures retrieval evidence coverage only. It does not measure generated-answer correctness and is not a formal production acceptance set.

Run with:

```powershell
.venv\Scripts\python.exe scripts\record_v3_business_runs.py `
  --cases evaluations\v3\holdout_v1\cases.jsonl `
  --output evaluations\v3\holdout_v1\first_run.json `
  --budget 1200
```
