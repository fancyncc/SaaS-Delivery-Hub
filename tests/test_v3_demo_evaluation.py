from contextlib import asynccontextmanager

import pytest

from scripts.evaluate_v3_demo import citation_errors, span_coverage, summarize
from scripts.record_v3_boundary_runs import cleanup_search, contains_fact, count_facts


def test_span_credit_requires_complete_statement_and_correct_source():
    spans = ["不得自动删除", "每 10 分钟重试一次，最多 3 次"]
    hits = [
        {"origin_id": "wrong", "text": spans[0]},
        {"origin_id": "gold", "text": "每 10 分钟重试一次"},
    ]
    assert span_coverage(spans, hits, "gold") == 0
    hits.append({"origin_id": "gold", "text": "疑似重复不得自动删除。"})
    assert span_coverage(spans, hits, "gold") == 1


def test_citation_validation_detects_fabricated_text_and_location():
    original = {
        "id": "unit", "document_id": "doc", "origin_id": "origin", "title": "Policy",
        "version": 1, "heading": "Limits", "kind": "paragraph", "node_id": "node",
        "location": {"page": 2}, "text": "Only 17 items allowed.",
    }
    assert citation_errors([original], {"unit": original}, {}) == []
    changed = {**original, "text": "Only 999 items allowed.", "location": {"page": 1}}
    reasons = {e["reason"] for e in citation_errors([changed], {"unit": original}, {})}
    assert reasons == {"source_position_mismatch", "source_text_changed"}
    assert citation_errors([{**original, "source_chunk_ids": ["missing"]}], {"unit": original}, {})[0]["reason"] == "unknown_source_unit"


def test_scope_noanswer_and_budget_failures_are_reported():
    row = {
        "necessary_total": 0, "returned_evidence": True, "permission_leaks": 1,
        "citation_errors": [{"reason": "source_text_changed"}],
        "final_tokens": 601, "budget": 600, "warm_latency_ms": 42, "status": "evidence_found",
    }
    metrics = summarize([row])
    assert metrics["noanswer_returned_evidence"] == 1
    assert metrics["permission_leaks"] == metrics["citation_errors"] == metrics["budget_violations"] == 1


def test_boundary_annotations_require_source_key_and_protected_conditions():
    case = {"necessary_facts": ["17"], "protected_conditions": [], "source_keys": ["limit"]}
    hits = [{"origin_id": "gold", "text": "id: 17"}]
    assert count_facts(case, hits, "gold") == 0
    assert not contains_fact("limit: 170", "17")
    assert count_facts(case, [{"origin_id": "gold", "text": "limit: 17"}], "gold") == 1
    case = {"necessary_facts": ["Stop before inspection"],
            "protected_conditions": ["Stop before inspection", "Emergency shutdown is exempt"],
            "source_keys": ["conditions"]}
    assert count_facts(case, [{"origin_id": "gold", "text": "conditions: Stop before inspection"}], "gold") == 1


def test_pdf_fact_credit_requires_annotated_page_and_preserves_numbers():
    case = {"necessary_facts": ["at least 80 % coverage"], "source_keys": [], "required_page": 12}
    correct = {"origin_id": "gold", "text": "at\nleast 80 % coverage", "location": {"page": 12}}
    assert count_facts(case, [correct], "gold") == 1
    assert count_facts(case, [{**correct, "location": {"page": 15}}], "gold") == 0
    assert count_facts(case, [{**correct, "origin_id": "other"}], "gold") == 0
    assert not contains_fact("at least 800 % coverage", "80 %")


def test_business_source_checksums_and_draft_labels_are_explicit():
    import hashlib
    import json
    from pathlib import Path

    root = Path("evaluations/v3/business")
    registry = json.loads((root / "sources.json").read_text(encoding="utf-8"))
    assert not registry["independent_review_completed"] and not registry["customer_documents"]
    assert len(registry["sources"]) == 5
    for source in registry["sources"]:
        assert hashlib.sha256((root / source["document"]).read_bytes()).hexdigest() == source["sha256"]
    cases = [json.loads(line) for line in (root / "cases.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(cases) == 29 and sum(c["kind"] == "no_answer" for c in cases) == 5
    assert all(c["review_status"] == "agent_draft" and c["split"] == "development" for c in cases)


def test_business_comparison_rejects_invalid_experiment_and_changed_budget():
    from copy import deepcopy

    from scripts.summarize_v3_business_comparison import compare

    before = {"complete": True, "test_data_cleaned": True, "cases_sha256": "frozen",
              "sources": [], "identity": {k: "pinned" for k in (
                  "embedding", "embedding_revision", "reranker", "reranker_revision", "embedding_dimensions")},
              "rows": [{"id": "a", "budget": 1200, "necessary_total": 1}]}
    invalid = {**before, "valid_comparison": False}
    with pytest.raises(ValueError, match="valid runs"):
        compare(before, invalid)
    changed = deepcopy(before)
    changed["rows"][0]["budget"] = 1800
    with pytest.raises(ValueError, match="budgets"):
        compare(before, changed)


async def test_boundary_cleanup_cannot_delete_other_search_sources(monkeypatch):
    requests = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"count": 0, "failures": [], "timed_out": False}

    class Http:
        async def post(self, path, json):
            requests.append((path, json))
            return Response()

    @asynccontextmanager
    async def fake_client():
        yield Http()

    monkeypatch.setattr("scripts.record_v3_boundary_runs.client", fake_client)
    await cleanup_search([])
    assert not requests
    await cleanup_search(["generated-source-a", "generated-source-b"])
    assert len(requests) == 2
    assert all(payload == {"query": {"terms": {"document_id": ["generated-source-a", "generated-source-b"]}}}
               for _, payload in requests)
    assert "_delete_by_query" in requests[0][0] and requests[1][0].endswith("/_count")


async def test_boundary_cleanup_failure_is_not_reported_as_success(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"timed_out": True}

    class Http:
        async def post(self, *args, **kwargs):
            return Response()

    @asynccontextmanager
    async def fake_client():
        yield Http()

    monkeypatch.setattr("scripts.record_v3_boundary_runs.client", fake_client)
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await cleanup_search(["generated-source"])
