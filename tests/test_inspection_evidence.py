import json

import pytest
from fastapi import HTTPException

from backend import inspection_evidence as evidence
from backend.config import get_settings


def hit(identifier, text, score=2, ordinal=0):
    return dict(
        id=identifier,
        chunk_id=identifier,
        document_id="d",
        source_id="s",
        title="title",
        heading="heading",
        text=text,
        ordinal=ordinal,
        generation=1,
        rerank_score=score,
        score=0.02,
    )


async def fake_counts(texts):
    return [len(t) for t in texts]


async def setup_pipeline(monkeypatch, hits, extra=()):
    monkeypatch.setattr(get_settings(), "rag_mode", "real")

    async def retrieve(*args, **kwargs):
        assert kwargs["knowledge_only"] and kwargs["inspection_candidates"]
        return [dict(h) for h in hits]

    async def supplement(*args):
        return list(extra)

    monkeypatch.setattr(evidence, "retrieve", retrieve)
    monkeypatch.setattr(evidence, "supplement", supplement)
    monkeypatch.setattr(evidence, "token_counts", fake_counts)


async def test_not_forced_to_five_and_threshold(monkeypatch):
    await setup_pipeline(
        monkeypatch, [hit("a", "必须受理，不自动结案。"), hit("b", "无关内容", -3)]
    )
    result = await evidence.inspect(None, "t", "q", [], 600, threshold=0)
    assert [h["id"] for h in result["chunks"]] == ["a"]
    assert result["diagnostics"]["candidates"][1]["disposition"] == "相关性不足"
    assert result["evidence_text"] == evidence.serialize_evidence(result["chunks"])


@pytest.mark.parametrize(
    "hits,status", [([], "not_found"), ([hit("a", "irrelevant", -3)], "insufficient_relevance")]
)
async def test_empty_states(monkeypatch, hits, status):
    await setup_pipeline(monkeypatch, hits)
    result = await evidence.inspect(None, "t", "q", [], 600, threshold=0)
    assert result["status"] == status and result["chunks"] == []


async def test_budget_preserves_paragraphs_and_metadata(monkeypatch):
    await setup_pipeline(monkeypatch, [hit("a", "A" * 40 + "\n\n" + "B" * 100)])
    result = await evidence.inspect(None, "t", "q", [], 80, threshold=0)
    assert result["chunks"][0]["text"] == "A" * 40
    assert result["diagnostics"]["final_tokens"] == len(result["evidence_text"]) <= 80
    assert result["status"] == "budget_limited"


def test_overlap_only_same_document_and_adjacent():
    shared = "共同保留的完整上下文。" * 3
    first = hit("a", "开始" + shared, ordinal=0)
    second = hit("b", shared + "结束", ordinal=1)
    result, _ = evidence.deduplicate([first, second])
    assert result[1]["text"] == "结束"
    result, _ = evidence.deduplicate([first, {**second, "document_id": "other"}])
    assert result[1]["text"] == second["text"]
    result, _ = evidence.deduplicate([first, {**second, "ordinal": 3}])
    assert result[1]["text"] == second["text"]


def test_table_headers_and_row_integrity():
    text = "| 字段 | 约束 |\n| --- | --- |\n| id | 不可为空 |\n| value | 不超过 100 |"
    pieces = evidence.units(text)
    assert len(pieces) == 2
    assert all(p.startswith("| 字段 | 约束 |\n| --- | --- |") for p in pieces)
    assert "不超过 100" in pieces[1]


async def test_max_eight_and_duplicate_diagnostics(monkeypatch):
    await setup_pipeline(
        monkeypatch,
        [hit(str(i), f"unique {i}", ordinal=i) for i in range(10)] + [hit("dup", "unique 0")],
    )
    result = await evidence.inspect(None, "t", "q", [], 1800, threshold=0)
    assert len(result["chunks"]) == 8
    assert result["status"] == "budget_limited"
    assert result["diagnostics"]["candidates"][-1]["disposition"] == "重复内容"


def test_calibration_identity_and_release_gate(monkeypatch, tmp_path):
    path = tmp_path / "calibration.json"
    monkeypatch.setattr(get_settings(), "rag_inspection_calibration", str(path))
    for passed, identity in [(False, evidence.model_identity()), (True, {})]:
        path.write_text(json.dumps(dict(passed=passed, identity=identity, threshold=1)))
        with pytest.raises(HTTPException):
            evidence.calibrated_threshold()
    path.write_text(json.dumps(dict(passed=True, identity=evidence.model_identity(), threshold=1)))
    assert evidence.calibrated_threshold() == 1


async def test_service_failure_does_not_become_empty(monkeypatch):
    await setup_pipeline(monkeypatch, [hit("a", "x")])

    async def fail(texts):
        raise HTTPException(503, "service unavailable")

    monkeypatch.setattr(evidence, "token_counts", fail)
    with pytest.raises(HTTPException):
        await evidence.inspect(None, "t", "q", [], 600, threshold=0)


async def test_score_capability_required_but_old_call_compatible(monkeypatch):
    import httpx

    from backend import retrieval_models

    settings = get_settings()
    monkeypatch.setattr(settings, "reranker_mode", "online")
    monkeypatch.setattr(settings, "reranker_base_url", "http://test/v1")
    monkeypatch.setattr(settings, "reranker_model", "bge")
    original = httpx.AsyncClient
    monkeypatch.setattr(
        retrieval_models.httpx,
        "AsyncClient",
        lambda **kwargs: original(
            **kwargs,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"results": [{"index": 0}]})
            ),
        ),
    )
    hits = [hit("a", "source")]
    assert await retrieval_models.rank("q", hits) == hits
    with pytest.raises(HTTPException):
        await retrieval_models.rank("q", hits, with_scores=True)


async def test_validated_scores_preserve_original_rrf(monkeypatch):
    import httpx

    from backend import retrieval_models

    settings = get_settings()
    monkeypatch.setattr(settings, "reranker_mode", "online")
    monkeypatch.setattr(settings, "reranker_base_url", "http://test/v1")
    monkeypatch.setattr(settings, "reranker_model", "bge")
    original = httpx.AsyncClient
    monkeypatch.setattr(
        retrieval_models.httpx,
        "AsyncClient",
        lambda **kwargs: original(
            **kwargs,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "model": "bge",
                        "revision": settings.reranker_revision,
                        "results": [{"index": 0, "score": -2.5}],
                    },
                )
            ),
        ),
    )
    result = await retrieval_models.rank("q", [hit("a", "source")], with_scores=True)
    assert result[0]["rerank_score"] == -2.5 and result[0]["score"] == 0.02


async def test_invalid_remote_token_counts_fail(monkeypatch):
    import httpx

    from backend import retrieval_models

    settings = get_settings()
    monkeypatch.setattr(settings, "embedding_mode", "online")
    monkeypatch.setattr(settings, "embedding_base_url", "http://test/v1")
    monkeypatch.setattr(settings, "embedding_model", "bge")
    original = httpx.AsyncClient
    monkeypatch.setattr(
        retrieval_models.httpx,
        "AsyncClient",
        lambda **kwargs: original(
            **kwargs,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={"model": "bge", "revision": settings.embedding_revision, "counts": [-1]},
                )
            ),
        ),
    )
    with pytest.raises(HTTPException):
        await retrieval_models.token_counts(["test"])


async def test_api_budget_validation(client, monkeypatch):
    async def inspect(*args):
        return {"pipeline": "v3", "status": "index_unavailable", "chunks": [], "evidence_text": ""}

    monkeypatch.setattr("backend.rag_v3.inspect", inspect)
    response = await client.post(
        "/api/knowledge/inspect", json={"question": "项目工单字段", "evidence_budget": 1200}
    )
    assert response.status_code == 200
    response = await client.post(
        "/api/knowledge/inspect", json={"question": "项目工单字段", "evidence_budget": 999}
    )
    assert response.status_code == 422
    response = await client.post(
        "/api/knowledge/inspect", json={"question": "你好", "evidence_budget": 600}
    )
    assert response.status_code == 200 and response.json()["data"]["status"] == "not_needed"
