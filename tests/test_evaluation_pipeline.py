"""Adversarial evaluation checks: source binding, partial runs and honest acceptance."""

import copy
import json
from argparse import Namespace

import pytest

from backend.evaluation_acceptance import aggregate, junit_result, performance_result
from backend.evaluation_artifacts import (
    RunJournal,
    canonical,
    corpus_fingerprint,
    digest,
    freeze,
    read_rows,
    verify_frozen,
    write_once,
)
from backend.evaluation_scoring import (
    STAGES,
    answer_report,
    ranking_metrics,
    review_failures,
    score_observation,
    stable_unit_key,
)
from backend.evaluation_validation import calibration_rows, check_envelope, validate_recording
from backend.retrieval_evaluation import metric_failures, metrics
from backend.retrieval_policy import PIPELINE_SCHEMA
from scripts.evaluate_retrieval import main as evaluation_cli


@pytest.fixture
def corpus(tmp_path):
    document = tmp_path / "source.md"
    document.write_text("limit 17; only after approval", encoding="utf-8")
    case = {"id": "cutover", "document": "source.md", "format": "md", "language": "zh",
            "split": "validation", "kind": "conditions", "domain": "crm", "document_group": "crm",
            "necessary_facts": ["17"], "protected_conditions": ["only after approval"],
            "source_spans": [{"fact": fact, "document": "source.md", "locations": [{"page": 1}],
                              "quotes": ["limit 17; only after approval"]} for fact in ("17", "only after approval")],
            "source_keys": ["limit"], "review_status": "pending_independent_review"}
    cases = tmp_path / "cases.jsonl"
    cases.write_bytes(canonical(case) + b"\n")
    frozen = freeze(cases, {"budget": 1200, "identity": {"embedding": "pinned"}})
    unit = {"id": "unit", "document_id": "doc", "origin_id": "origin", "node_id": "node",
            "title": "source", "source": "source.md", "version": 1, "kind": "paragraph",
            "heading": "", "location": {"page": 1}, "text": document.read_text(encoding="utf-8")}
    snapshot = {"documents": {"doc": {"document": "source.md", "sha256": digest(document.read_bytes())}},
                "units": {"unit": unit}, "nodes": {"node": unit["text"]}}
    row = {**{k: case[k] for k in ("id", "format", "language", "split", "kind")},
           "pipeline_schema": PIPELINE_SCHEMA, "budget": 1200, "warm_latency_ms": 20,
           "outcome": "succeeded", "result": {"chunks": [unit], "diagnostics": {
               "final_tokens": 20, "evaluation_stages": {s: [unit] for s in STAGES if s not in {"index", "selected"}}}}}
    return cases, case, frozen, snapshot, row


def recording(corpus, tmp_path):
    _, _, frozen, snapshot, row = corpus
    journal = RunJournal(tmp_path / "run.json", frozen)
    journal.append(row)
    return journal.finish("succeeded", cleanup=True, source_snapshot=snapshot)


def test_question_tamper_detected_before_collection(corpus):
    path, _, frozen, _, _ = corpus
    path.write_text(path.read_text(encoding="utf-8").replace("cutover", "changed"), encoding="utf-8")
    with pytest.raises(ValueError, match="corpus_changed"):
        verify_frozen(frozen, path)


def test_declared_formats_cannot_inflate_binary_document_coverage(corpus):
    path, case, _, _, _ = corpus
    case["format"] = "xlsx"
    path.write_bytes(canonical(case) + b"\n")
    with pytest.raises(ValueError, match="format differs"):
        corpus_fingerprint(path)


def test_source_registry_rejects_changed_question_hash(corpus):
    path, _, frozen, _, _ = corpus
    registry = {"cases_sha256": "0" * 64, "sources": [{"document": "source.md", "sha256": frozen["corpus"]["sources_sha256"]["source.md"]}]}
    path.with_name("sources.json").write_bytes(canonical(registry))
    with pytest.raises(ValueError, match="question checksum"):
        corpus_fingerprint(path)


@pytest.mark.parametrize("component", ["config", "implementation_sha256", "policy_sha256"])
def test_postrun_provenance_change_is_invalid(corpus, component):
    path, _, frozen, _, _ = corpus
    changed = copy.deepcopy(frozen)
    changed[component] = {"changed": True} if component != "policy_sha256" else "changed"
    with pytest.raises(ValueError):
        verify_frozen(changed, path, config=frozen["config"])


def test_failed_journal_keeps_completed_rows_and_missing_ids(corpus, tmp_path):
    _, _, frozen, _, row = corpus
    frozen = copy.deepcopy(frozen)
    frozen["corpus"]["case_ids"].append("not_started")
    journal = RunJournal(tmp_path / "partial.json", frozen)
    journal.append(row)
    report = journal.finish("interrupted", cleanup=False, errors=["CancelledError"])
    assert not report["complete"] and report["missing_case_ids"] == ["not_started"]
    assert read_rows(journal.directory / "observations.jsonl") == [row]
    with pytest.raises(ValueError, match="incomplete"):
        check_envelope(report)
    with pytest.raises(FileExistsError):
        RunJournal(journal.output, frozen)


def test_reserving_output_and_exclusive_artifacts_prevent_overwrite(corpus, tmp_path):
    path = tmp_path / "result.json"
    write_once(path, {"original": True})
    with pytest.raises(FileExistsError):
        write_once(path, {"original": False})
    assert json.loads(path.read_bytes()) == {"original": True}
    reserved = RunJournal(tmp_path / "unfinished.json", corpus[2])
    with pytest.raises(FileExistsError):
        RunJournal(reserved.output, corpus[2])


@pytest.mark.parametrize("field", ["rows", "frozen", "source_snapshot"])
def test_report_tampering_cannot_be_scored(corpus, tmp_path, field):
    report = recording(corpus, tmp_path)
    if field == "rows":
        report[field][0]["result"]["chunks"] = []
    else:
        report[field]["changed"] = True
    with pytest.raises(ValueError, match="tampered"):
        check_envelope(report)


def test_evidence_budget_must_match_frozen_comparison_budget(corpus, tmp_path):
    report = recording(corpus, tmp_path)
    report["rows"][0]["budget"] = 1800
    report["rows_sha256"] = digest(canonical(report["rows"]))
    with pytest.raises(ValueError, match="budget_differs"):
        check_envelope(report)


def test_scores_recomputed_from_source_spans_and_counts_cannot_be_forged(corpus):
    _, case, _, snapshot, row = corpus
    scored = score_observation(case, row, snapshot)
    assert scored["necessary_total"] == 2 and scored["covered_necessary"] == 2
    row["necessary_total"] = 1
    with pytest.raises(ValueError, match="reported_count_mismatch"):
        score_observation(case, row, snapshot)


def test_wrong_source_and_changed_text_never_gain_coverage(corpus):
    _, case, _, snapshot, row = corpus
    changed = copy.deepcopy(row)
    selected = copy.deepcopy(row["result"]["chunks"][0])
    selected["text"] = "limit 170; only after approval"
    changed["result"]["chunks"] = [selected]
    scored = score_observation(case, changed, snapshot)
    assert scored["covered_necessary"] == 0 and scored["source_mismatches"] > 0
    assert scored["fact_corruptions"] > 0
    selected["document_id"] = "private"
    changed["result"]["diagnostics"]["evaluation_stages"]["rrf"] = [selected]
    scored = score_observation(case, changed, snapshot)
    assert scored["permission_leaks"] > 0 and scored["covered_necessary"] == 0


def test_stage_loss_localizes_rerank_cutoff(corpus):
    _, case, _, snapshot, row = corpus
    row["result"]["diagnostics"]["evaluation_stages"]["rerank"] = []
    scored = score_observation(case, row, snapshot)
    assert scored["stage_losses"]["rerank"] == {"lost": [0, 1], "recovered": []}
    del row["result"]["diagnostics"]["evaluation_stages"]["rrf"]
    with pytest.raises(ValueError, match="missing_stage"):
        score_observation(case, row, snapshot)


def test_gold_span_location_prevents_credit_from_other_occurrence(corpus):
    _, case, _, snapshot, row = corpus
    case["source_spans"][0]["locations"] = [{"page": 2}]
    scored = score_observation(case, row, snapshot)
    assert scored["covered_necessary"] == 1


@pytest.mark.parametrize("key,value", [("warm_latency_ms", float("nan")), ("budget", 200)])
def test_malformed_measurements_are_invalid(corpus, key, value):
    _, case, _, snapshot, row = corpus
    row[key] = value
    with pytest.raises(ValueError):
        score_observation(case, row, snapshot)


def test_ranking_requires_complete_labels_and_has_real_rank_semantics(corpus):
    _, case, _, snapshot, row = corpus
    hit = row["result"]["chunks"][0]
    assert ranking_metrics(case, [hit], snapshot)["status"] == "not_run"
    case.update(qrels_complete=True, qrels={stable_unit_key(hit, snapshot): 3})
    assert ranking_metrics(case, [hit], snapshot)["ndcg"] == 1
    distractor = {**hit, "text": "irrelevant"}
    case["qrels"][stable_unit_key(distractor, snapshot)] = 0
    ranked = ranking_metrics(case, [distractor, hit], snapshot)
    assert ranked["mrr"] == .5 and 0 < ranked["ndcg"] < 1


def test_agent_authored_labels_do_not_count_as_independent_review(corpus):
    _, case, frozen, _, _ = corpus
    case["review_status"] = "independently_reviewed"
    frozen["review"] = {"cases_sha256": frozen["corpus"]["cases_sha256"],
                        "validation_previously_exposed": False, "cases": [
                            {"id": case["id"], "status": "approved", "reviewer_kind": "agent",
                             "author": "a", "reviewer": "b", "reviewed_at": "2026-10-11",
                             "label_sha256": digest(canonical(case))}]}
    assert "independent_review_pending" in review_failures([case], frozen)
    frozen["review"]["cases"][0]["reviewer_kind"] = "human"
    assert not review_failures([case], frozen)
    frozen["review"]["validation_previously_exposed"] = True
    assert "fresh_validation_required" in review_failures([case], frozen)


def test_missing_baseline_not_a_zero_cost_success(corpus):
    _, case, _, snapshot, row = corpus
    values = metrics([score_observation(case, row, snapshot)])
    assert values["old_coverage"] is None and values["token_reduction"] is None
    assert "coverage_90_and_baseline" in metric_failures(values)


def test_token_optimization_is_optional_but_evidence_is_required():
    good = {"recall": 1, "coverage": 1, "old_coverage": 1, "noanswer_false_positive": 0,
            "token_reduction": -.2, "latency_ratio_p95": 1, "violations": 0}
    assert not metric_failures(good)
    assert metric_failures(good, require_token_reduction=True) == ["token_reduction_25"]


def test_locked_recording_stays_failed_with_draft_labels_and_small_corpus(corpus, tmp_path):
    path, case, _, _, _ = corpus
    report = recording(corpus, tmp_path)
    evaluated = validate_recording([case], report, path)
    assert not evaluated["passed"]
    assert {"paired_baseline_not_run", "insufficient_question_count", "independent_review_pending"} <= set(evaluated["failures"])


def test_cli_accepts_report_envelope_and_exits_nonzero_for_failed_acceptance(corpus, tmp_path):
    report = recording(corpus, tmp_path)
    output = tmp_path / "score.json"
    assert evaluation_cli(["validate", "--cases", str(corpus[0]), "--runs", str(tmp_path / "run.json"), "--output", str(output)]) == 1
    assert json.loads(output.read_bytes())["run_id"] == report["run_id"]


def test_legacy_counts_are_diagnostic_only_and_fail_closed(corpus, tmp_path):
    runs = tmp_path / "legacy.json"
    runs.write_text('{"rows": []}', encoding="utf-8")
    output = tmp_path / "score.json"
    assert evaluation_cli(["validate", "--cases", str(corpus[0]), "--runs", str(runs), "--output", str(output)]) == 1
    assert json.loads(output.read_bytes())["failures"] == ["unbound_legacy_recording"]


def test_oracle_and_retrieved_answers_need_bound_human_review(corpus):
    _, case, _, _, raw = corpus
    observations = [{"id": case["id"], "mode": mode, "outcome": "succeeded", "provider_model": "model",
                     "answer": "limit 17; only after approval", "citations": [{"source_id": "unit"}],
                     "evidence": raw["result"]["chunks"], "usage": None}
                    for mode in ("retrieved", "oracle")]
    result = answer_report([case], observations)
    assert not result["passed"] and "answer_semantic_review_pending" in result["failures"]
    review = {"rows_sha256": digest(canonical(observations)), "reviewer_kind": "human",
              "author": "a", "reviewer": "b", "reviewed_at": "2026-10-11", "rows": [
                  {"id": case["id"], "mode": mode, "facts_complete": True, "claims_supported": True,
                   "conditions_preserved": True, "abstention_correct": True} for mode in ("retrieved", "oracle")]}
    assert answer_report([case], observations, review=review)["passed"]
    observations[0]["citations"] = [{"source_id": "unknown"}]
    assert "answer_evidence_violation" in answer_report([case], observations, review=review)["failures"]


def test_junit_uses_actual_cases_instead_of_forged_totals(tmp_path):
    path = tmp_path / "suite.xml"
    path.write_text('<testsuite tests="462" failures="0"><testcase name="not_executed"><skipped/></testcase></testsuite>', encoding="utf-8")
    result = junit_result(path)
    assert result["status"] == "failed" and result["counts"]["tests"] == 1 and result["counts"]["skipped"] == 1


def test_acceptance_rejects_missing_stale_and_skipped_reports():
    code = {"backend/file.py": "current"}
    reports = {"agent": {"schema": 1, "run_id": "a", "status": "passed", "implementation_sha256": code,
                         "counts": {"skipped": 1}},
               "memory": {"schema": 1, "run_id": "b", "passed": True, "suite": "memory", "implementation_sha256": {}}}
    result = aggregate(["agent", "memory", "browser"], reports, expected_implementation=code)
    assert not result["production_acceptance"]
    assert result["checks"]["browser"]["status"] == "not_run"
    assert result["checks"]["memory"]["reasons"] == ["stale_implementation"]


def test_performance_requires_cold_reset_load_and_actual_usage():
    limits = {"p95_ms": 200, "max_error_rate": 0, "minimum_samples": 1, "concurrency": 2,
              "input_per_million": 1, "output_per_million": 2, "currency": "USD"}
    samples = [{"phase": phase, "latency_ms": 100, "outcome": "succeeded", "concurrency": 2,
                "cache_reset_verified": True, "usage": {"input_tokens": 100, "output_tokens": 10}}
               for phase in ("cold", "warm", "concurrent")]
    assert performance_result(samples, limits)["status"] == "passed"
    samples[0]["cache_reset_verified"] = False
    samples[1]["usage"] = None
    result = performance_result(samples, limits)
    assert result["status"] == "failed" and result["cost"] is None
    assert "cold:cache_reset_unverified" in result["failures"]


async def test_collector_preserves_prior_rows_when_later_format_fails(corpus, tmp_path, monkeypatch):
    from backend.config import get_settings
    from scripts import record_retrieval_boundary_runs as collector

    path, case, frozen, snapshot, row = corpus
    frozen["config"] = {"test": True}
    write_once(tmp_path / "freeze.json", frozen)
    monkeypatch.setattr(collector, "runtime_config", lambda budget: {"test": True})
    monkeypatch.setattr(get_settings(), "rag_mode", "real")
    cases = [case, {**case, "id": "second", "format": "pdf"}]
    monkeypatch.setattr(collector, "read_rows", lambda path: cases)
    async def fake_record(fmt, selected, args):
        if fmt == "pdf":
            raise TimeoutError()
        args.source_snapshot.update(snapshot)
        row.update(necessary_total=2, recalled_necessary=2, covered_necessary=2,
                   returned_evidence=True, permission_leaks=0, citation_errors=[],
                   final_tokens=20, status="evidence_found")
        args.journal.append(row)
        return [row], []
    monkeypatch.setattr(collector, "record_format", fake_record)
    args = Namespace(cases=path, freeze=tmp_path / "freeze.json", output=tmp_path / "failed.json",
                     formats=("md", "pdf"), budget=1200)
    assert await collector.main(args) == 1
    report = json.loads(args.output.read_bytes())
    assert report["rows"] == [row] and report["errors"] == ["TimeoutError"]
    assert not report["test_data_cleaned"] and not report["complete"]


async def test_actual_answer_adapter_records_both_evidence_modes_and_provider_usage(corpus, tmp_path, monkeypatch):
    import httpx

    from backend import intelligence
    from backend.config import get_settings
    from backend.evaluation_artifacts import runtime_config
    from scripts import evaluate_answers

    path, case, frozen, _, _ = corpus
    case["question"] = "What is the limit and its condition?"
    path.write_bytes(canonical(case) + b"\n")
    settings = get_settings()
    for name, value in {"model_mode": "real", "model_name": "probe", "model_base_url": "https://example.test/v1",
                        "model_api_key": "not-for-artifacts", "model_api_style": "chat_completions"}.items():
        monkeypatch.setattr(settings, name, value)
    frozen.update(freeze(path, runtime_config(1200)))
    original = intelligence._structured
    requests = []
    def provider(request):
        body = json.loads(request.content)
        requests.append(body)
        assert "necessary_facts" not in str(body) and "protected_conditions" not in str(body)
        return httpx.Response(200, json={"id": "response", "model": "probe-pinned",
            "usage": {"prompt_tokens": 80, "completion_tokens": 15},
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
                "answer": "limit 17; only after approval [1]", "citation_ids": ["unit"]})}}]})
    async def adapter(task, source, schema, **kwargs):
        return await original(task, source, schema, transport=httpx.MockTransport(provider))
    monkeypatch.setattr(intelligence, "_structured", adapter)
    raw = recording(corpus, tmp_path)
    output = tmp_path / "answers.json"
    assert await evaluate_answers.main(["collect", "--cases", str(path), "--runs", str(tmp_path / "run.json"), "--output", str(output)]) == 0
    answers = json.loads(output.read_bytes())
    assert len(requests) == 2 and {r["mode"] for r in answers["answer_rows"]} == {"retrieved", "oracle"}
    assert all(r["usage"] == {"input_tokens": 80, "output_tokens": 15} for r in answers["answer_rows"])
    assert "not-for-artifacts" not in output.read_text(encoding="utf-8")
    assert answers["retrieval_run_id"] == raw["run_id"]
    score = tmp_path / "answer-score.json"
    assert await evaluate_answers.main(["score", "--cases", str(path), "--runs", str(output), "--output", str(score)]) == 1
    assert json.loads(score.read_bytes())["failures"] == ["answer_semantic_review_pending"]


def test_provider_usage_sums_retries_without_inventing_missing_counts():
    from backend.model_observations import measured_usage

    receipts = [{"usage": {"prompt_tokens": 10, "completion_tokens": 2}},
                {"usage": {"input_tokens": 20, "output_tokens": 3}}]
    assert measured_usage(receipts) == {"input_tokens": 30, "output_tokens": 5}
    receipts.append({"usage": None})
    assert measured_usage(receipts) is None


async def test_stage_trace_is_scoped_and_reauthorization_removes_private_diagnostics():
    import asyncio

    from backend.retrieval_diagnostics import capture, enabled, evaluation_trace, snapshot

    assert not enabled()
    with evaluation_trace():
        hit = {"id": "allowed", "document_id": "public", "text": "original"}
        capture("rrf", [hit, {"id": "hidden", "document_id": "private", "text": "secret"}])
        hit["text"] = "mutated"
        assert snapshot({"public"})["rrf"] == [{"id": "allowed", "document_id": "public", "text": "original"}]
        async def other_run():
            with evaluation_trace():
                assert not snapshot({"public"})["rrf"]
        await asyncio.create_task(other_run())
        assert len(snapshot({"public"})["rrf"]) == 1
    assert not enabled()


async def test_candidate_classifier_can_be_evaluated_without_authorizing_release(tmp_path, monkeypatch):
    from backend.config import get_settings
    from backend.retrieval_release import FEATURES
    from scripts.record_retrieval_boundary_runs import evaluation_inspect

    path = tmp_path / "classifier.json"
    path.write_bytes(canonical({"weights": dict.fromkeys(FEATURES, 0), "intercept": 0, "threshold": .5}))
    monkeypatch.setattr(get_settings(), "retrieval_release", str(path))
    monkeypatch.setattr(get_settings(), "retrieval_relevance_mode", "calibrated")
    async def run(session, tenant, question, projects, budget, config):
        assert config["classifier"]["threshold"] == .5
        return {"chunks": []}
    def release():
        raise AssertionError("Candidate must not authorize production")
    monkeypatch.setattr("backend.knowledge_retrieval.run", run)
    monkeypatch.setattr("backend.knowledge_retrieval.release", release)
    assert await evaluation_inspect(None, "tenant", "query", 1200) == {"chunks": []}


def test_calibration_uses_judged_candidates_and_excludes_validation_features(corpus, tmp_path):
    from backend.retrieval_release import FEATURES

    path, case, frozen, snapshot, row = corpus
    case["split"] = row["split"] = "calibration"
    hit = row["result"]["chunks"][0]
    hit["features"] = dict.fromkeys(FEATURES, 1.0)
    row["result"]["diagnostics"]["candidates"] = [hit]
    raw = recording(corpus, tmp_path)
    with pytest.raises(ValueError, match="explicit complete relevance"):
        calibration_rows([case], raw)
    case.update(qrels_complete=True, qrels={stable_unit_key(hit, snapshot): 0})
    judged = calibration_rows([case], raw)
    assert len(judged) == 1 and judged[0]["relevant"] is False
    case["split"] = "validation"
    with pytest.raises(ValueError, match="not recorded"):
        calibration_rows([case], raw)
