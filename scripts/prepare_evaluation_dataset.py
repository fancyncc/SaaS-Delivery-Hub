"""Prepare source-pinned review drafts; exposed fixtures never become a new blind set."""

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.document_parser import parse
from backend.evaluation_artifacts import canonical, digest, read_rows, write_once
from backend.evaluation_scoring import contains_fact, facts


def prepare(source_cases, development_cases, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    rows, sources, parsed = [], {}, {}

    def add(case, path, group, split=None):
        document = (path.parent / case["document"]).resolve()
        if not document.is_relative_to(path.parent.resolve()):
            raise ValueError("Source path must remain within corpus")
        relative = "documents/" + group + "/" + case["document"].replace("\\", "/")
        target = output / relative
        if relative not in sources:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(document, target)
            sources[relative] = {"document": relative, "sha256": digest(target.read_bytes()),
                                 "source_kind": "exposed_regression_fixture", "license": "repository test fixture"}
            parsed[relative] = parse(document.name, document.read_bytes())
        row = {**case, "document": relative, "split": split or case["split"],
               "review_status": "pending_independent_review", "label_author": "agent",
               "provenance": "review_draft_from_exposed_regression"}
        row.setdefault("domain", group + ":" + document.stem)
        row.setdefault("document_group", group + ":" + document.stem)
        row.setdefault("content_family", row["document_group"])
        row.setdefault("project_group", row["document_group"])
        spans = []
        for fact in facts(row):
            candidates = [node for node in parsed[relative].nodes
                          if contains_fact(node.context + "\n" + node.text, fact)]
            spans.append({"fact": fact, "document": relative,
                          "locations": [node.location for node in candidates],
                          "quotes": [node.context + "\n" + node.text for node in candidates],
                          "status": "draft" if candidates else "needs_source_review"})
        row["source_spans"] = spans
        rows.append(row)

    source_cases = Path(source_cases).resolve()
    original = read_rows(source_cases)
    for case in original:
        add(case, source_cases, "regression")
        if case["kind"] == "noanswer":
            # Add the missing Chinese no-answer slice; humans must verify the negative label.
            alternate = {**case, "id": case["id"] + "-zh",
                         "language": "zh", "question": "该实施事项的保险保费是多少？资料没有明确说明时请指出缺失信息。"}
            add(alternate, source_cases, "regression")
    if development_cases:
        development_cases = Path(development_cases).resolve()
        for case in read_rows(development_cases):
            add(case, development_cases, "development", "development")
    raw = b"".join(canonical(row) + b"\n" for row in rows)
    (output / "cases.jsonl").write_bytes(raw)
    case_hash = digest(raw)
    write_once(output / "sources.json", {"cases_sha256": case_hash, "sources": list(sources.values()),
               "scope": "expanded review draft; previously exposed sources", "independent_review_completed": False,
               "validation_previously_exposed": True, "customer_documents": False})
    write_once(output / "review.json", {"cases_sha256": case_hash, "validation_previously_exposed": True,
               "cases": [{"id": row["id"], "label_sha256": digest(canonical(row)), "author": "agent",
                          "reviewer": None, "reviewer_kind": "human", "reviewed_at": None, "status": "pending"}
                         for row in rows]})
    readiness = {"question_count": len(rows), "source_count": len(sources), "release_ready": False,
                 "split_counts": {s: sum(r["split"] == s for r in rows)
                                  for s in ("development", "calibration", "validation")},
                 "validation_noanswer": {lang: sum(r["split"] == "validation" and r["kind"] == "noanswer"
                                                    and r["language"] == lang for r in rows) for lang in ("zh", "en")},
                 "remaining": ["independent_source_and_label_review", "fresh_locked_validation_sources",
                               "complete_relevance_labels", "permission_and_complex_document_cases"]}
    write_once(output / "readiness.json", readiness)
    return readiness


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path("evaluations/retrieval/cases.jsonl"))
    parser.add_argument("--development-cases", type=Path, default=Path("evaluations/retrieval/holdout_v1/cases.jsonl"))
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.cases, args.development_cases, args.output_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
