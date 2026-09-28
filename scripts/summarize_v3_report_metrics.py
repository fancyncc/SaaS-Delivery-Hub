"""Recompute descriptive report statistics from the four recorded local runs."""

import hashlib
import json
import math
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = {
    "source_before": "data/evaluations/business-baseline/report.json",
    "source_after": "data/evaluations/business-deployed/report.json",
    "boundary_before": (
        "data/evaluations/9c5693299e8d4b9d970e35b2a78b7d66/boundary-1200.json"
    ),
    "boundary_after": "data/evaluations/boundary-context4/boundary-1200.json",
}
EXPECTED_SHA256 = {
    "source_before": "486cf0eab189ec99302d9f03a011233f59ba42fe1c7138c01a20cccd038f3254",
    "source_after": "66c2ad3da18fa2e9c8ec5996978ec2f24d4e5141a81b8690953298a76f3901f3",
    "boundary_before": "4b53aa84ab5af2cf2efd3dc1030818e22cb0f70a585514666bf96bad42dfb959",
    "boundary_after": "84e8d55fa9c30168235db615f94cd44e3e086c1ec1261d56d25299affc9a9348",
}


def describe(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean": None, "p50": None, "p95": None, "max": None}
    ordered = sorted(values)
    return {
        "n": len(values),
        "mean": round(statistics.mean(values), 4),
        "p50": round(statistics.median(values), 4),
        "p95": round(ordered[math.ceil(0.95 * len(ordered)) - 1], 4),
        "max": round(ordered[-1], 4),
    }


def summarize(rows: list[dict]) -> dict:
    answerable = [row for row in rows if row["kind"] not in {"no_answer", "noanswer"}]
    original_tokens = [
        row["result"]["diagnostics"].get("original_tokens", 0) for row in rows
    ]
    final_tokens = [row["final_tokens"] for row in rows]
    return {
        "questions": len(rows),
        "answerable_questions": len(answerable),
        "complete_fact_questions": sum(
            row["covered_necessary"] == row["necessary_total"] for row in answerable
        ),
        "partial_fact_questions": sum(
            0 < row["covered_necessary"] < row["necessary_total"] for row in answerable
        ),
        "zero_fact_questions": sum(row["covered_necessary"] == 0 for row in answerable),
        "required_facts": sum(row["necessary_total"] for row in rows),
        "candidate_facts": sum(row["recalled_necessary"] for row in rows),
        "selected_facts": sum(row["covered_necessary"] for row in rows),
        "warm_latency_ms": describe([row["warm_latency_ms"] for row in rows]),
        "final_evidence_tokens": describe(final_tokens),
        "nonempty_evidence_tokens": describe(
            [row["final_tokens"] for row in rows if row["returned_evidence"]]
        ),
        "candidate_original_tokens": describe(original_tokens),
        "candidate_filtering_token_reduction_micro": (
            round(1 - sum(final_tokens) / sum(original_tokens), 6)
            if sum(original_tokens)
            else None
        ),
    }


def main() -> None:
    output = {
        "scope": "Descriptive statistics of recorded retrieval runs; not release approval",
        "latency_p95_method": "nearest rank: sorted[ceil(0.95*n)-1]",
        "token_scope": "BGE retrieval evidence tokenizer; excludes generation input/output",
        "datasets": {},
    }
    for name, relative in RUNS.items():
        raw = (ROOT / relative).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != EXPECTED_SHA256[name]:
            raise ValueError(f"Recorded report checksum changed: {relative}")
        report = json.loads(raw)
        if not report.get("complete") or report.get("valid_comparison") is False:
            raise ValueError(f"Incomplete or invalid run: {relative}")
        rows = report["rows"]
        output["datasets"][name] = {
            "raw_report": relative,
            "raw_report_sha256": digest,
            "generated_at": report["generated_at"],
            "pipeline_schema": report.get("pipeline_schema", rows[0]["result"]["pipeline_schema"]),
            "stats": summarize(rows),
            "per_language": {
                language: summarize([row for row in rows if row["language"] == language])
                for language in sorted({row["language"] for row in rows})
            },
            "per_kind": {
                kind: summarize([row for row in rows if row["kind"] == kind])
                for kind in sorted({row["kind"] for row in rows})
            },
        }
    destination = ROOT / "evaluations/v3/detailed_report_metrics.json"
    destination.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(destination)


if __name__ == "__main__":
    main()
