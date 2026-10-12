"""Locked acceptance of bound recordings, with no missing-check success defaults."""

from collections import Counter

from backend.evaluation_artifacts import canonical, digest, verify_frozen
from backend.evaluation_scoring import review_failures, score_observation
from backend.retrieval_evaluation import evaluate


def check_envelope(report):
    if report.get("schema") != 1 or not report.get("run_id"):
        raise ValueError("unbound_legacy_recording")
    if report.get("frozen_sha256") != digest(canonical(report["frozen"])):
        raise ValueError("frozen_manifest_tampered")
    if report.get("rows_sha256") != digest(canonical(report["rows"])):
        raise ValueError("observations_tampered")
    if (not report.get("complete") or report.get("status") != "succeeded"
            or report.get("test_data_cleaned") is not True or report.get("errors")):
        raise ValueError("incomplete_or_unclean_recording")
    ids = [r["id"] for r in report["rows"]]
    if len(set(ids)) != len(ids) or set(ids) != set(report["frozen"]["corpus"]["case_ids"]):
        raise ValueError("incomplete_or_duplicate_observations")
    if any(row.get("budget") != report["frozen"]["config"]["budget"] for row in report["rows"]):
        raise ValueError("observation_budget_differs_from_freeze")
    snapshot = report["source_snapshot"]
    if report.get("source_snapshot_sha256") != digest(canonical(snapshot)):
        raise ValueError("source_snapshot_tampered")
    registered = report["frozen"]["corpus"]["sources_sha256"]
    for source in snapshot["documents"].values():
        if registered.get(source["document"]) != source["sha256"]:
            raise ValueError("indexed_source_mismatch")
    if {s["document"] for s in snapshot["documents"].values()} != set(registered):
        raise ValueError("indexed_source_missing")


def validate_recording(cases, report, cases_path, *, baseline=None, policy_path=None):
    check_envelope(report)
    kwargs = {"policy_path": policy_path} if policy_path else {}
    verify_frozen(report["frozen"], cases_path, **kwargs)
    frozen = report["frozen"]
    policy = frozen["policy"]
    case_map = {c["id"]: c for c in cases}
    scored = [score_observation(case_map[r["id"]], r, report["source_snapshot"])
              for r in report["rows"] if case_map[r["id"]]["split"] == "validation"]
    errors = []
    if baseline is not None:
        check_envelope(baseline)
        bf = baseline["frozen"]
        if (bf["corpus"] != frozen["corpus"] or bf["environment"] != frozen["environment"]
                or bf["config"]["budget"] != frozen["config"]["budget"]
                or bf["policy_sha256"] != frozen["policy_sha256"]):
            raise ValueError("unpaired_baseline")
        old = {r["id"]: score_observation(case_map[r["id"]], r, baseline["source_snapshot"])
               for r in baseline["rows"]}
        for row in scored:
            previous = old[row["id"]]
            row.update(old_covered_necessary=previous["covered_necessary"], old_tokens=previous["final_tokens"],
                       old_warm_latency_ms=previous["warm_latency_ms"])
    elif policy["require_paired_baseline"]:
        errors.append("paired_baseline_not_run")
    if not scored:
        errors.append("locked_validation_not_run")
    result = evaluate(cases, scored, policy=policy)
    if policy["require_independent_review"]:
        errors.extend(review_failures(cases, frozen))
    if len(cases) < policy["minimum_questions"]:
        errors.append("insufficient_question_count")
    if len({c.get("domain") for c in cases}) < policy["minimum_domains"]:
        errors.append("insufficient_domain_count")
    for lang in ("zh", "en"):
        count = sum(r["language"] == lang and r["necessary_total"] == 0 for r in scored)
        if count < policy["minimum_noanswer_per_language"]:
            errors.append(lang + ":insufficient_noanswer_cases")
    for kind, values in result["by_kind"].items():
        if kind != "noanswer" and (values["recall"] < policy["recall_minimum"]
                                   or values["coverage"] < policy["coverage_minimum"]):
            errors.append(kind + ":evidence_quality")
    for row in scored:
        if len(row["stages"]["index"]) != row["necessary_total"]:
            errors.append("gold_spans_missing_from_index")
        if policy["require_token_reduction"]:
            reduction = result["metrics"].get("token_reduction")
            if reduction is None or reduction < policy["token_reduction_target"]:
                errors.append("token_reduction_target")
    result.update(
        failures=sorted(set(result["failures"] + errors)), schema=1,
        run_id=report["run_id"], frozen_sha256=report["frozen_sha256"],
        cases_sha256=frozen["corpus"]["cases_sha256"], sources_sha256=frozen["corpus"]["sources_sha256"],
        environment=frozen["environment"], identity=frozen["config"]["identity"],
        implementation_sha256=frozen["implementation_sha256"], policy_sha256=frozen["policy_sha256"],
        classifier_sha256=frozen["config"].get("classifier_sha256"), budget=frozen["config"]["budget"],
        independent_review={"approved": not review_failures(cases, frozen)},
        question_count=len(cases), domain_count=len({c.get("domain") for c in cases}),
        formats=sorted({c["format"] for c in cases}), model_comparison_passed=False,
        outcomes=dict(Counter(r.get("outcome", "not_run") for r in report["rows"])),
        rows=scored, scope="locked_retrieval_validation", release_approved=False,
    )
    result["passed"] = not result["failures"]
    return result


def calibration_rows(cases, report):
    """Use explicit qrels for calibration features; never infer relevance from retrieval scores."""
    import math

    from backend.evaluation_scoring import stable_unit_key
    from backend.retrieval_release import FEATURES

    check_envelope(report)
    rows = []
    case_map = {c["id"]: c for c in cases}
    for observation in report["rows"]:
        case = case_map[observation["id"]]
        if case["split"] != "calibration":
            continue
        if not case.get("qrels_complete") or not case.get("qrels"):
            raise ValueError("Calibration requires explicit complete relevance labels")
        for hit in observation["result"].get("diagnostics", {}).get("candidates", []):
            if "features" not in hit:
                continue
            key = stable_unit_key(hit, report["source_snapshot"])
            grade = case["qrels"].get(key)
            if type(grade) is not int or not 0 <= grade <= 3:
                raise ValueError("Unjudged calibration candidate")
            values = hit["features"]
            if set(values) != set(FEATURES) or any(type(v) not in (int, float) or not math.isfinite(v) for v in values.values()):
                raise ValueError("Invalid calibration features")
            rows.append({"id": case["id"], "split": "calibration", "relevant": grade > 0,
                         "features": values, "unit_key": key})
    if not rows:
        raise ValueError("Calibration candidates not recorded")
    return rows
