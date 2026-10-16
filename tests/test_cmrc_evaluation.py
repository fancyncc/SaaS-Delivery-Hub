
import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.cmrc_evaluation import (
    answer_scores,
    compact_prediction,
    evidence_hit,
    load_corpus,
    longest_match,
    prepare,
    score_run,
    source_intervals,
)
from backend.evaluation_artifacts import canonical, digest


def corpus(tmp_path, *, bad_offset=False):
    raw = canonical({"data": [{"title": "示例", "paragraphs": [{
        "context": "甲在北京。乙在上海。",
        "qas": [{"id": "q1", "question": "甲在哪里？", "answers": [
            {"text": "北京", "answer_start": 0 if bad_offset else 2}]}],
    }]}]})
    path = tmp_path / "corpus"
    manifest = prepare(raw, path, limit=0)
    cases, _ = load_corpus(path)
    return path, cases, manifest


def recording(cases, manifest, rows, snapshot=None):
    snapshot = snapshot or {"documents": {str(i): {"document": d, "sha256": sha}
                                        for i, (d, sha) in enumerate(manifest["sources_sha256"].items())}}
    frozen = {"manifest_sha256": digest(canonical(manifest)), "case_ids": [c["id"] for c in cases]}
    return {"run_id": "test", "frozen": frozen, "frozen_sha256": digest(canonical(frozen)),
            "rows": rows, "rows_sha256": digest(canonical(rows)), "source_snapshot": snapshot,
            "source_snapshot_sha256": digest(canonical(snapshot)), "status": "succeeded",
            "complete": True, "test_data_cleaned": True}


def test_contiguous_match_is_not_subsequence_or_bag_overlap():
    assert longest_match(list("甲乙丙"), list("甲丁乙戊丙")) == 1
    assert answer_scores(["甲乙丙"], "甲丁乙戊丙")["f1"] == .25
    assert answer_scores(["上海", "北京"], "北京。")["em"] == 1


def test_prediction_removes_citations_without_extracting_a_gold_substring():
    assert compact_prediction("答案是北京。[1]") == "答案是北京。"
    assert answer_scores(["北京"], compact_prediction("答案是北京。[1]"))["em"] == 0


def test_prepare_is_deterministic_and_preserves_invalid_labels(tmp_path):
    path, cases, manifest = corpus(tmp_path, bad_offset=True)
    assert cases[0]["answers"][0] == {"text": "北京", "answer_start": 0, "valid_position": False}
    assert manifest["questions_without_valid_position"] == 1
    result = prepare((path / "cmrc2018_dev.json").read_bytes(), tmp_path / "again", limit=0)
    assert result["cases_sha256"] == manifest["cases_sha256"]


def test_modified_source_is_rejected(tmp_path):
    path, cases, _ = corpus(tmp_path)
    (path / cases[0]["document"]).write_text("改变", encoding="utf-8")
    with pytest.raises(ValueError, match="source changed"):
        load_corpus(path)


def test_evidence_requires_correct_document_and_source_position(tmp_path):
    path, cases, _ = corpus(tmp_path)
    case = cases[0]
    snapshot = {"documents": {"d": {"document": case["document"]}, "wrong": {"document": "other.txt"}}}
    assert evidence_hit(case, [{"document_id": "d", "text": "甲在北京。"}], snapshot, path)
    assert not evidence_hit(case, [{"document_id": "wrong", "text": "甲在北京。"}], snapshot, path)
    assert not evidence_hit(case, [{"document_id": "d", "text": "北京在北方"}], snapshot, path)
    assert source_intervals("北京和北京", "北京") == []
    assert source_intervals("甲在北京。乙在上海。", "甲在北京。\n乙在上海。") == [(0, 10)]


def test_errors_and_missing_questions_are_zero_not_removed(tmp_path):
    path, cases, manifest = corpus(tmp_path)
    failed = recording(cases, manifest, [{"id": "q1", "outcome": "failed", "answer": "北京"}])
    result = score_run(path, failed)
    assert result["metrics"]["em"] == 0
    assert result["metrics"]["f1"] == 0
    assert result["error_rate"] == 1
    assert result["metrics"]["answer_evidence_hit_at_k"]["5"] == 0
    assert not result["complete"]
    result = score_run(path, recording(cases, manifest, []))
    assert result["error_rate"] == 1
    assert result["question_count"] == 1


def test_bad_offset_excluded_only_from_evidence_not_answer_score(tmp_path):
    path, cases, manifest = corpus(tmp_path, bad_offset=True)
    report = recording(cases, manifest, [{"id": "q1", "outcome": "succeeded", "answer": "北京"}])
    result = score_run(path, report)
    assert result["metrics"]["em"] == 100
    assert result["metrics"]["evidence_eligible_questions"] == 0
    assert result["metrics"]["answer_evidence_hit_at_k"]["5"] is None


def test_tampered_duplicate_and_unknown_observations_rejected(tmp_path):
    path, cases, manifest = corpus(tmp_path)
    row = {"id": "q1", "outcome": "succeeded", "answer": "北京"}
    report = recording(cases, manifest, [row])
    report["rows"][0]["answer"] = "上海"
    with pytest.raises(ValueError, match="changed"):
        score_run(path, report)
    with pytest.raises(ValueError, match="duplicate recorded"):
        score_run(path, recording(cases, manifest, [row, row]))
    with pytest.raises(ValueError, match="Unknown"):
        score_run(path, recording(cases, manifest, [{**row, "id": "unknown"}]))


def test_usage_cost_require_actual_receipts_and_explicit_prices(tmp_path):
    path, cases, manifest = corpus(tmp_path)
    row = {"id": "q1", "outcome": "succeeded", "answer": "北京",
           "usage": {"input_tokens": 100, "output_tokens": 10}, "latency_ms": 123}
    report = recording(cases, manifest, [row])
    result = score_run(path, report)
    assert result["cost"]["value"] is None
    assert result["latency"]["latency_ms"]["p95_ms"] == 123
    result = score_run(path, report, input_price=2, output_price=8, currency="CNY")
    assert result["cost"]["value"] == .00028
    assert result["production_acceptance"] is False


def test_scorer_matches_recorded_official_v6_examples():
    """Outputs checked against upstream v6; CI needs no downloaded dataset/assets."""
    pairs = [("甲乙丙", "甲丁乙戊丙", 0, .25), ("北京", "北京。", 1, 1),
             ("北京", "", 0, 0), ("冠军：甲乙", "甲乙", 0, 2 / 3),
             ("上海", "北京", 0, 0), ("甲，乙", "甲乙", 1, 1)]
    for reference, prediction, em, f1 in pairs:
        ours = answer_scores([reference], prediction)
        assert ours["em"] == em
        assert ours["f1"] == f1


def test_score_cli_writes_all_reports_without_nltk(tmp_path):
    path, cases, manifest = corpus(tmp_path)
    report = recording(cases, manifest, [{"id": "q1", "outcome": "succeeded", "answer": "北京[1]"}])
    report["frozen"]["config"] = {"model_name": "recorded-model",
                                    "identity": {"embedding": "recorded-embedding", "reranker": "recorded-reranker"}}
    report["frozen_sha256"] = digest(canonical(report["frozen"]))
    runs, output = tmp_path / "run.json", tmp_path / "score.json"
    runs.write_bytes(canonical(report))
    root = Path(__file__).resolve().parents[1]
    command = [sys.executable, str(root / "scripts/evaluate_cmrc.py"), "score",
               "--corpus", str(path), "--runs", str(runs), "--output", str(output)]
    subprocess.run(command, cwd=tmp_path, check=True, capture_output=True, text=True, timeout=30)
    scored = json.loads(output.read_bytes())
    assert scored["complete"] is True
    assert scored["metrics"]["em"] == scored["metrics"]["f1"] == 100
    assert scored["scoring"]["nltk_version"] is None
    assert scored["scoring"]["tokenizer"]["reference_nltk_version"] == "3.10.3"
    assert output.with_suffix(".md").exists()
    assert json.loads((tmp_path / "score-predictions.json").read_bytes()) == {"q1": "北京"}
    # An existing report must still be immutable.
    retry = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert retry.returncode != 0
    assert "Output exists" in retry.stderr
    assert json.loads(output.read_bytes()) == scored
