"""Export paired development observations, without approving a quality release."""

import argparse
import hashlib
import json
from pathlib import Path


def compare(before, after):
    for report in (before, after):
        if not report.get("complete") or not report.get("test_data_cleaned") or report.get("valid_comparison") is False:
            raise ValueError("Only complete, cleaned, valid runs may be compared")
    if before["cases_sha256"] != after["cases_sha256"] or before["sources"] != after["sources"]:
        raise ValueError("Sources and labels must be identical")
    for key in ("embedding", "embedding_revision", "reranker", "reranker_revision", "embedding_dimensions"):
        if before["identity"][key] != after["identity"][key]:
            raise ValueError("This comparison requires identical models")
    old = {row["id"]: row for row in before["rows"]}
    new = {row["id"]: row for row in after["rows"]}
    if old.keys() != new.keys() or any(old[k]["budget"] != new[k]["budget"] or old[k]["necessary_total"] != new[k]["necessary_total"] for k in old):
        raise ValueError("Case IDs, budgets and required facts must match")
    return {
        "release_approved": False, "independent_review_completed": False,
        "scope": after["scope"], "customer_documents": False,
        "cases_sha256": after["cases_sha256"], "sources": after["sources"],
        "before": {k: before[k] for k in ("generated_at", "identity", "pipeline_schema", "implementation_sha256", "metrics", "per_format", "per_language")},
        "after": {k: after[k] for k in ("generated_at", "identity", "pipeline_schema", "implementation_sha256", "metrics", "per_format", "per_language")},
        "per_case": [{"id": key, "required": old[key]["necessary_total"],
                      "before_covered": old[key]["covered_necessary"], "after_covered": new[key]["covered_necessary"],
                      "before_returned_evidence": old[key]["returned_evidence"], "after_returned_evidence": new[key]["returned_evidence"],
                      "after_status": new[key]["status"]} for key in old],
    }


def main(args):
    before = json.loads(args.before.read_text(encoding="utf-8"))
    after = json.loads(args.after.read_text(encoding="utf-8"))
    result = compare(before, after)
    result["raw_report_sha256"] = {"before": hashlib.sha256(args.before.read_bytes()).hexdigest(),
                                   "after": hashlib.sha256(args.after.read_bytes()).hexdigest()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("evaluations/v3/business/comparison.json"))
    main(parser.parse_args())
