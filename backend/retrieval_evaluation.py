"""Offline calibration and locked validation; never invoked by user queries."""

import math
import statistics
from collections import Counter

from backend.retrieval_release import FEATURES, RELEASE_FORMATS, classify


def calibrate(rows, epochs=800):
    if not rows or any(r["split"] != "calibration" for r in rows):
        raise ValueError("Training accepts calibration rows only")
    if {r["relevant"] for r in rows} != {True, False}:
        raise ValueError("Both label classes required")
    weights = {k: 0.0 for k in FEATURES}
    intercept = 0.0
    for _ in range(epochs):
        gradient = {k: 0.0 for k in FEATURES}
        bias = 0.0
        for row in rows:
            x = row["features"]
            z = intercept + sum(weights[k] * x[k] for k in FEATURES)
            error = 1 / (1 + math.exp(-max(-60, min(60, z)))) - int(row["relevant"])
            bias += error
            for k in FEATURES:
                gradient[k] += error * x[k]
        for k in FEATURES:
            weights[k] -= 0.03 * (gradient[k] / len(rows) + 0.001 * weights[k])
        intercept -= 0.03 * bias / len(rows)
    model = {"weights": weights, "intercept": intercept, "threshold": 0.0}
    positive = sorted(classify(r["features"], model)[1] for r in rows if r["relevant"])
    # Highest empirical threshold retaining at least 95% of calibration positives.
    model["threshold"] = positive[int(len(positive) * 0.05)]
    return model


def metrics(rows):
    answerable = [r for r in rows if r["necessary_total"] > 0]
    noanswer = [r for r in rows if r["necessary_total"] == 0]
    simple = [r for r in rows if r["kind"] == "fact" and r["necessary_total"] > 0]
    required = sum(r["necessary_total"] for r in answerable)
    def ratio(key):
        return sum(r[key] for r in answerable) / required if required else 0
    paired = bool(rows) and all(all(k in r for k in
                  ("old_tokens", "old_covered_necessary", "old_warm_latency_ms")) for r in rows)
    old = statistics.median(r["old_tokens"] for r in simple) if simple and paired else 0
    new = statistics.median(r["final_tokens"] for r in simple) if simple else 0
    latency = sorted(r["warm_latency_ms"] for r in rows if r.get("old_warm_latency_ms", 0) > 0)
    old_latency = sorted(
        r["old_warm_latency_ms"] for r in rows if r.get("old_warm_latency_ms", 0) > 0
    )
    p95 = max(0, math.ceil(0.95 * len(latency)) - 1)
    return {
        "recall": ratio("recalled_necessary"),
        "coverage": ratio("covered_necessary"),
        "old_coverage": ratio("old_covered_necessary") if paired else None,
        "noanswer_false_positive": sum(bool(r["returned_relevant"]) for r in noanswer)
        / len(noanswer)
        if noanswer
        else 1,
        "token_reduction": 1 - new / old if old else None,
        "latency_ratio_p95": latency[p95] / old_latency[p95]
        if latency and len(latency) == len(rows)
        else None,
        "baseline_measured": paired,
        "answerable_complete_coverage": sum(r["covered_necessary"] == r["necessary_total"] for r in answerable) / len(answerable) if answerable else None,
        "violations": sum(
            r["permission_leaks"]
            + r["source_mismatches"]
            + r["fact_corruptions"]
            + int(r["final_tokens"] > r["budget"])
            for r in rows
        ),
    }


def metric_failures(m, *, require_token_reduction=False, policy=None):
    policy = policy or {}
    return [
        name
        for name, ok in {
            "recall_95": m["recall"] >= policy.get("recall_minimum", .95),
            "coverage_90_and_baseline": m.get("old_coverage") is not None and m["coverage"] >= max(policy.get("coverage_minimum", .9), m["old_coverage"]),
            "noanswer_fp_5": m["noanswer_false_positive"] <= policy.get("noanswer_false_positive_maximum", .05),
            "token_reduction_25": not (require_token_reduction or policy.get("require_token_reduction")) or (m.get("token_reduction") is not None and m["token_reduction"] >= policy.get("token_reduction_target", .25)),
            "latency_1_5": m.get("latency_ratio_p95") is not None and m["latency_ratio_p95"] <= policy.get("latency_ratio_maximum", 1.5),
            "zero_violations": m["violations"] == 0,
        }.items()
        if not ok
    ]


def validate_manifest(cases):
    errors = []
    if len(cases) < 192:
        errors.append("fewer_than_192_questions")
    if len({c["id"] for c in cases}) != len(cases):
        errors.append("duplicate_question_ids")
    if len({c["domain"] for c in cases}) < 6:
        errors.append("fewer_than_6_domains")
    if {c["format"] for c in cases} != set(RELEASE_FORMATS):
        errors.append("format_coverage")
    format_counts = Counter(c["format"] for c in cases)
    if any(format_counts[fmt] < 24 for fmt in RELEASE_FORMATS):
        errors.append("insufficient_format_questions")
    if {c["language"] for c in cases} != {"zh", "en"}:
        errors.append("language_coverage")
    if any(c.get("review_status") != "independently_reviewed" for c in cases):
        errors.append("independent_review_pending")
    train = [c for c in cases if c["split"] == "calibration"]
    valid = [c for c in cases if c["split"] == "validation"]
    for key in ("domain", "document_group"):
        if {c[key] for c in train} & {c[key] for c in valid}:
            errors.append(key + "_split_leak")
    if not train or not valid:
        errors.append("missing_split")
    if sum("XL-107" in str(c) for c in cases) > len(cases) * 0.1:
        errors.append("demo_overrepresented")
    return errors


def evaluate(cases, rows, *, policy=None):
    from backend.retrieval_policy import PIPELINE_SCHEMA

    errors = validate_manifest(cases)
    if any(row.get("pipeline_schema") != PIPELINE_SCHEMA for row in rows):
        errors.append("pipeline_version_mismatch")
    expected = {c["id"] for c in cases if c["split"] == "validation"}
    if {r["id"] for r in rows} != expected or len(rows) != len(expected):
        errors.append("incomplete_locked_validation")
    case_map = {c["id"]: c for c in cases}
    for row in rows:
        if row["id"] not in case_map or row["format"] != case_map[row["id"]]["format"]:
            errors.append("run_source_format_mismatch")
        count_keys = (
            "necessary_total",
            "recalled_necessary",
            "covered_necessary",
            "permission_leaks",
            "source_mismatches",
            "fact_corruptions",
            "final_tokens",
        )
        if any(type(row[k]) is not int or row[k] < 0 for k in count_keys):
            raise ValueError("invalid observation counts")
        if any(k in row and (type(row[k]) is not int or row[k] < 0)
               for k in ("old_tokens", "old_covered_necessary")):
            raise ValueError("invalid baseline counts")
        from backend.evaluation_scoring import facts

        if row["id"] in case_map and row["necessary_total"] != len(facts(case_map[row["id"]])):
            raise ValueError("observed total differs from gold labels")
        if any(
            row.get(k, 0) > row["necessary_total"]
            for k in ("recalled_necessary", "covered_necessary", "old_covered_necessary")
        ):
            raise ValueError("observed facts exceed annotated total")
        if row["budget"] not in (600, 1200, 1800):
            raise ValueError("invalid evidence budget")
    overall = metrics(rows) if rows else {}
    per_format = {}
    if rows:
        errors.extend(metric_failures(overall, policy=policy))
        for fmt in RELEASE_FORMATS:
            subset = [r for r in rows if r["format"] == fmt]
            if len(subset) < (policy or {}).get("minimum_validation_per_format", 12):
                errors.append("insufficient_validation_format_" + fmt)
                continue
            per_format[fmt] = metrics(subset)
            errors.extend(fmt + ":" + e for e in metric_failures(per_format[fmt], policy=policy))
    by_language = {lang: metrics([r for r in rows if r["language"] == lang])
                   for lang in ("zh", "en") if any(r["language"] == lang for r in rows)}
    by_kind = {kind: metrics([r for r in rows if r["kind"] == kind])
               for kind in sorted({r["kind"] for r in rows})}
    for lang, values in by_language.items():
        errors.extend(lang + ":" + e for e in metric_failures(values, policy=policy))
    return {
        "passed": not errors,
        "failures": errors,
        "metrics": overall,
        "per_format": per_format,
        "by_language": by_language,
        "by_kind": by_kind,
        "pipeline_schema": PIPELINE_SCHEMA,
    }


def compare_models(current, multilingual):
    """Compare verified reports on the same locked questions and machine profile."""
    if (
        current["cases_sha256"] != multilingual["cases_sha256"]
        or current["environment"] != multilingual["environment"]
        or current.get("sources_sha256") != multilingual.get("sources_sha256")
        or current.get("budget") != multilingual.get("budget")
    ):
        raise ValueError("Model comparison requires paired corpus and environment")
    eligible = []
    for name, report in (("current", current), ("multilingual", multilingual)):
        if not report["passed"] or metric_failures(report["metrics"]):
            continue
        if any(fmt not in report.get("per_format", {}) for fmt in RELEASE_FORMATS):
            continue
        if any(
            metric_failures(report["per_format"][f]) for f in RELEASE_FORMATS
        ):
            continue
        if (report.get("schema") != 1 or not report.get("run_id")
                or not report.get("independent_review", {}).get("approved")
                or any(lang not in report.get("by_language", {}) for lang in ("zh", "en"))):
            continue
        if any(metric_failures(report["by_language"][lang]) for lang in ("zh", "en")):
            continue
        macro = sum(report["by_language"][lang]["recall"] for lang in ("zh", "en")) / 2
        eligible.append((macro, name, report))
    if not eligible:
        return {
            "passed": False,
            "reason": "Neither model combination passes validation",
            "selected": None,
        }
    # A negligible recall difference does not justify switching away from the current pair.
    eligible.sort(key=lambda row: (round(row[0], 3), row[1] == "current"), reverse=True)
    winner = eligible[0]
    return {
        "passed": True,
        "selected": winner[1],
        "macro_recall": winner[0],
        "identity": winner[2]["identity"],
        "run_id": winner[2]["run_id"],
        "frozen_sha256": winner[2]["frozen_sha256"],
        "cases_sha256": winner[2]["cases_sha256"],
        "sources_sha256": winner[2]["sources_sha256"],
    }
