"""Aggregate engineering evidence without treating absent or skipped checks as passes."""

import json
import math
from pathlib import Path
from xml.etree import ElementTree

from backend.evaluation_artifacts import canonical, digest, implementation_checksums


def junit_result(path):
    root = ElementTree.parse(path).getroot()
    tests = list(root.iter("testcase"))
    counts = {"tests": len(tests), "failed": 0, "errors": 0, "skipped": 0}
    names = []
    for case in tests:
        names.append(case.get("classname", "") + "::" + case.get("name", ""))
        for tag, key in (("failure", "failed"), ("error", "errors"), ("skipped", "skipped")):
            counts[key] += int(case.find(tag) is not None)
    passed = counts["tests"] > 0 and not any(counts[k] for k in ("failed", "errors", "skipped"))
    return {"status": "passed" if passed else "failed", "counts": counts, "test_cases": names,
            "junit_sha256": digest(Path(path).read_bytes())}


def performance_result(samples, limits):
    errors = []
    phases = {}
    if not limits or any(key not in limits for key in ("p95_ms", "max_error_rate", "minimum_samples", "concurrency")):
        return {"status": "not_run", "reason": "frozen_environment_limits_required"}
    for key in ("p95_ms", "max_error_rate", "input_per_million", "output_per_million", "max_total_cost"):
        if key in limits and (type(limits[key]) not in (int, float) or not math.isfinite(limits[key]) or limits[key] < 0):
            raise ValueError("invalid_performance_limits")
    if (type(limits["minimum_samples"]) is not int or limits["minimum_samples"] < 1
            or type(limits["concurrency"]) is not int or limits["concurrency"] < 2
            or limits["max_error_rate"] > 1):
        raise ValueError("invalid_performance_sample_limits")
    for phase in ("cold", "warm", "concurrent"):
        rows = [r for r in samples if r.get("phase") == phase]
        if len(rows) < limits["minimum_samples"]:
            errors.append(phase + ":insufficient_samples")
        if any(type(r.get("latency_ms")) not in (int, float) or not math.isfinite(r["latency_ms"])
               or r["latency_ms"] < 0 for r in rows):
            raise ValueError("invalid_latency")
        latencies = sorted(r["latency_ms"] for r in rows)
        failed = sum(r.get("outcome") != "succeeded" for r in rows)
        p95 = latencies[max(0, math.ceil(len(latencies) * .95) - 1)] if rows else None
        error_rate = failed / len(rows) if rows else None
        if p95 is None or p95 > limits["p95_ms"] or error_rate > limits["max_error_rate"]:
            errors.append(phase + ":performance_gate")
        if phase == "cold" and any(r.get("cache_reset_verified") is not True for r in rows):
            errors.append("cold:cache_reset_unverified")
        if phase == "concurrent" and any(r.get("concurrency", 0) < limits["concurrency"] for r in rows):
            errors.append("concurrent:load_unverified")
        phases[phase] = {"samples": len(rows), "errors": failed, "error_rate": error_rate, "p95_ms": p95}
    usage = [r.get("usage") for r in samples]
    measured = bool(usage) and all(isinstance(u, dict) and
                                 type(u.get("input_tokens")) is int and type(u.get("output_tokens")) is int
                                 and u["input_tokens"] >= 0 and u["output_tokens"] >= 0 for u in usage)
    cost = None
    if measured and "input_per_million" in limits and "output_per_million" in limits:
        cost = sum(u["input_tokens"] * limits["input_per_million"] +
                   u["output_tokens"] * limits["output_per_million"] for u in usage) / 1_000_000
    else:
        errors.append("measured_cost_not_run")
    if cost is not None and "max_total_cost" in limits and cost > limits["max_total_cost"]:
        errors.append("cost_gate")
    return {"status": "passed" if not errors else "failed", "failures": errors, "phases": phases,
            "usage_measured": measured, "cost": cost, "cost_currency": limits.get("currency")}


def aggregate(required, reports, *, expected_implementation=None, expected_frozen=None):
    expected_implementation = expected_implementation or implementation_checksums()
    checks = {}
    scopes = {"postgres_rls": "real_postgres", "worker_recovery": "real_worker_recovery",
              "backup_restore": "real_backup_restore", "distributed_trace": "real_cross_process_trace",
              "real_services": "real_service_protocol", "retrieval": "locked_retrieval_validation",
              "answers": "locked_answer_validation", "performance": "target_environment_performance"}
    for name in required:
        report = reports.get(name)
        if report is None:
            checks[name] = {"status": "not_run"}
            continue
        status = report.get("status", "passed" if report.get("passed") is True else "failed")
        reasons = []
        if report.get("schema") != 1 or not report.get("run_id"):
            reasons.append("unbound_report")
        if report.get("implementation_sha256") != expected_implementation:
            reasons.append("stale_implementation")
        if expected_frozen and report.get("frozen_sha256") != expected_frozen:
            reasons.append("unpaired_acceptance")
        if (report.get("counts") or {}).get("skipped", 0) or report.get("errors") or report.get("failures"):
            reasons.append("failed_or_skipped_checks")
        if name in scopes and report.get("scope") != scopes[name]:
            reasons.append("acceptance_scope_mismatch")
        if name in {"backend", "agent", "memory", "browser"} and report.get("suite") != name:
            reasons.append("suite_mismatch")
        checks[name] = {"status": "failed" if reasons else status, "reasons": reasons,
                        "report_sha256": digest(canonical(report)), "counts": report.get("counts")}
    passed = bool(checks) and bool(expected_frozen) and all(c["status"] == "passed" for c in checks.values())
    return {"schema": 1, "passed": passed, "production_acceptance": passed,
            "checks": checks, "implementation_sha256": expected_implementation,
            "frozen_sha256": expected_frozen,
            "failures": [name for name, check in checks.items() if check["status"] != "passed"]
                        + ([] if expected_frozen else ["frozen_acceptance_required"])}


def load_reports(items):
    reports = {}
    for item in items:
        name, path = item.split("=", 1)
        if name in reports:
            raise ValueError("duplicate_acceptance_check")
        reports[name] = json.loads(Path(path).read_bytes())
    return reports
