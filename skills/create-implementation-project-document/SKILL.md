---
name: create-implementation-project-document
description: Create, review, or normalize a structured SaaS customer implementation project brief for onboarding, configuration, data migration, training, go-live, and acceptance. Use when drafting a new implementation project, converting loose customer notes into required and optional fields, checking whether an implementation brief is complete, or preparing project inputs for the implementation Agent.
metadata:
  version: "1"
---

# Create Implementation Project Document

Produce an implementation brief that is complete enough for Agent planning and human approval. Never invent missing customer facts.

## Workflow

1. Read `references/project-brief-schema.md` before drafting or validating a brief.
2. Extract facts from customer materials and map them to the schema.
3. Separate required fields from optional fields.
4. Mark unavailable required facts as `待补充`; do not infer names, dates, headcount, or approvals.
5. Normalize departments into a deduplicated list.
6. Write concrete implementation requirements as observable business outcomes, including actors, scenarios, actions, permission boundaries, constraints, and expected results. Do not replace them with generic module names.
7. Record migration scope and acceptance criteria separately from general requirements.
8. Run the completeness checklist before returning the document.

## Output Rules

- Preserve the customer's terminology and language.
- Use ISO dates (`YYYY-MM-DD`).
- Use exact employee counts when supplied; otherwise mark them `待补充`.
- Keep optional fields present but empty when the output must match the API payload.
- State assumptions explicitly and keep them out of confirmed facts.
- A draft may contain unknown values. Do not put `待补充` into typed date or integer API fields; return null and a missing-field list until the customer supplies the facts. Use check_project_brief before constructing a ProjectCreate submission.
- Do not include credentials, production secrets, or unnecessary personal data.

## Completeness Checklist

- Project and customer names are present.
- A customer contact and valid contact email are present.
- Employee count and target go-live date are present.
- At least one department and a concrete implementation requirement are present.
- Requirements contain enough detail to identify organization, permissions, workflow, migration, or training needs.
- Migration scope and acceptance criteria are either documented or visibly marked as optional/undecided.
- Risks, assumptions, and unresolved questions are distinguishable from confirmed requirements.

## Return Format

When preparing data for this repository, return a JSON-compatible object that follows the field names in `references/project-brief-schema.md`. When preparing a human-readable document, use the same field order and label required fields clearly.
