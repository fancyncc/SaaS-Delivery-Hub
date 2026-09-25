import io
import json

import httpx
import pytest
from fastapi import HTTPException
from pypdf import PdfWriter

from backend import retrieval_models as models
from backend.chat import ChatAnswer, answer_question, extract_document
from backend.config import Settings, get_settings
from backend.intelligence import structured
from backend.knowledge import index_identity


def mock_http(monkeypatch, handler):
    monkeypatch.setattr(get_settings(), "embedding_mode", "online")
    monkeypatch.setattr(get_settings(), "reranker_mode", "online")
    original = httpx.AsyncClient
    monkeypatch.setattr(models.httpx, "AsyncClient", lambda **kw: original(
        **kw, transport=httpx.MockTransport(handler)))


async def test_embedding_order_normalization_and_query_template(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "embedding_base_url", "https://embedding.example/v1")
    monkeypatch.setattr(s, "embedding_model", "test")
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        return httpx.Response(200, json={"data": [
            {"index": i, "embedding": [float(i + 1)] + [0.0] * 511}
            for i in reversed(range(len(payload["input"])))]})
    mock_http(monkeypatch, handler)
    result = await models.embeddings(["甲", "乙"])
    assert result == [[1.0] + [0.0] * 511] * 2
    await models.embeddings(["甲"], query=True)
    assert calls[0]["input"] == ["甲", "乙"]
    assert calls[1]["input"] == [s.embedding_query_instruction + "甲"]


@pytest.mark.parametrize("vector", [[0] * 512, [1] * 5, [float("nan")] * 512, [True] * 512])
def test_invalid_vectors_rejected(vector):
    with pytest.raises(HTTPException):
        models.validate_vectors([vector], 1, 512)


async def test_reranker_rejects_missing_or_duplicate_results(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "reranker_base_url", "https://rerank.example/v1")
    monkeypatch.setattr(s, "reranker_model", "test")
    mock_http(monkeypatch, lambda request: httpx.Response(200,
        json={"results": [{"index": 0}, {"index": 0}]}))
    with pytest.raises(HTTPException) as error:
        await models.rank("问题", [{"text": "甲"}, {"text": "乙"}])
    assert error.value.status_code == 502


async def test_local_model_does_not_use_http(monkeypatch):
    class Array:
        def tolist(self):
            return [[2.0] + [0.0] * 511]
    class Fake:
        def encode(self, inputs, **kwargs):
            assert inputs == ["文档"] and kwargs["normalize_embeddings"]
            return Array()
    monkeypatch.setattr(get_settings(), "embedding_mode", "local")
    monkeypatch.setattr(models, "_local", lambda *args: Fake())
    monkeypatch.setattr(models.httpx, "AsyncClient", lambda **kwargs: pytest.fail("unexpected HTTP"))
    assert (await models.embeddings(["文档"]))[0][0] == 1.0


async def test_qwen_chat_protocol_and_schema_validation(monkeypatch):
    s = get_settings()
    for name, value in {"model_base_url": "https://llm.example/v1", "model_name": "qwen-plus",
                        "model_api_key": "test-only", "model_api_style": "chat_completions"}.items():
        monkeypatch.setattr(s, name, value)
    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload["response_format"] == {"type": "json_object"}
        assert "JSON" in payload["messages"][0]["content"]
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
            "message": {"content": '{"answer":"测试回答","citation_ids":[]}'}}]})
    answer = await structured("测试", {}, ChatAnswer, transport=httpx.MockTransport(handler))
    assert answer.answer == "测试回答"


def test_embedding_signature_changes_with_query_instruction(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "rag_mode", "real")
    before = index_identity()
    monkeypatch.setattr(s, "embedding_query_instruction", "changed")
    assert index_identity() != before


async def test_real_rag_never_returns_offline_excerpt(monkeypatch):
    monkeypatch.setattr(get_settings(), "rag_mode", "real")
    monkeypatch.setattr(get_settings(), "model_mode", "deterministic")
    with pytest.raises(HTTPException) as error:
        await answer_question("问题", [], "", [])
    assert error.value.status_code == 503


def test_pdf_without_text_is_explicitly_unsupported():
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(output)
    with pytest.raises(HTTPException) as error:
        extract_document("scan.pdf", output.getvalue())
    assert error.value.status_code == 422 and "OCR" in error.value.detail


def test_config_env_path_is_absolute():
    assert Settings.model_config["env_file"].is_absolute()


async def test_bm25_filter_has_tenant_project_and_generation(monkeypatch):
    from backend import lexical_index
    s = get_settings()
    monkeypatch.setattr(s, "opensearch_url", "https://search.example")
    def handler(request):
        body = json.loads(request.content)
        filters = body["query"]["bool"]["filter"]
        assert {"term": {"tenant_id": "tenant-a"}} in filters
        assert {"term": {"index_version": "generation-2"}} in filters
        assert {"terms": {"project_id": ["company", "project-a"]}} in filters
        assert body["_source"] is False
        return httpx.Response(200, json={"hits": {"hits": [{"_id": "piece-a"}]}})
    mock_http(monkeypatch, handler)
    assert await lexical_index.search("tenant-a", ["project-a"], "generation-2", "词项") == ["piece-a"]


async def test_model_server_keys_are_isolated_and_model_id_checked(monkeypatch):
    from backend.retrieval_server import app
    s = get_settings()
    monkeypatch.setattr(s, "embedding_api_key", "embedding-test")
    monkeypatch.setattr(s, "reranker_api_key", "rerank-test")
    monkeypatch.setattr(s, "embedding_mode", "local")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        payload = {"model": "unconfigured-model", "input": ["文档"]}
        assert (await client.post("/v1/embeddings", json=payload)).status_code == 401
        assert (await client.post("/v1/embeddings", json=payload,
            headers={"Authorization": "Bearer rerank-test"})).status_code == 401
        assert (await client.post("/v1/embeddings", json=payload,
            headers={"Authorization": "Bearer embedding-test"})).status_code == 503


def test_mixed_pdf_does_not_silently_skip_scanned_page(monkeypatch):
    import pypdf
    class Page:
        def __init__(self, text, images):
            self.text, self.images = text, images
        def extract_text(self):
            return self.text
    class Reader:
        is_encrypted = False
        pages = [Page("第一个文字页", []), Page("", ["image"])]
    monkeypatch.setattr(pypdf, "PdfReader", lambda stream: Reader())
    with pytest.raises(HTTPException) as error:
        extract_document("mixed.pdf", b"test")
    assert "第 2 页" in error.value.detail and "OCR" in error.value.detail
    Reader.pages = [Page("可提取文字", ["image"])]
    result = extract_document("mixed.pdf", b"test")
    assert "可提取文字" in result and "图片内容未识别" in result


async def test_mock_retrieval_never_contacts_model(monkeypatch):
    from backend.intelligence import embed
    from backend.knowledge import rerank
    monkeypatch.setattr(get_settings(), "rag_mode", "mock")
    monkeypatch.setattr(get_settings(), "embedding_model", "configured-but-disabled")
    monkeypatch.setattr(models.httpx, "AsyncClient", lambda **kw: pytest.fail("unexpected HTTP"))
    assert await embed("文档") is None
    hits = [{"text": "文档"}]
    assert await rerank("问题", hits) == hits


async def test_old_embedding_dimension_fails_before_inference(monkeypatch):
    monkeypatch.setattr(get_settings(), "embedding_dimensions", 1024)
    monkeypatch.setattr(models, "_local", lambda *args: pytest.fail("must reject before loading"))
    with pytest.raises(HTTPException, match="512"):
        await models.embeddings(["文档"])


async def test_cpu_rerank_budget_covers_every_candidate_and_metadata(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "reranker_mode", "local")
    monkeypatch.setattr(s, "reranker_max_windows", 3)
    seen = []

    class Tokenizer:
        def encode(self, value, **kwargs):
            return list(value)

        def decode(self, tokens):
            return "".join(tokens)

        def num_special_tokens_to_add(self, pair):
            return 4

    class Scores:
        def tolist(self):
            return [0.1, 0.9, 0.2]

    class Model:
        tokenizer = Tokenizer()

        def predict(self, pairs, **kwargs):
            seen.extend(pairs)
            assert kwargs["batch_size"] == 4
            assert all(len(q) + len(p) + 4 <= 256 for q, p in pairs)
            return Scores()

    monkeypatch.setattr(models, "_local", lambda *args: Model())
    hits = [{"text": "甲" * 2000, "title": "文档甲", "heading": "章节甲"},
            {"text": "乙" * 2000, "title": "文档乙", "heading": "章节乙"}]
    assert await models.rank("原始问题", hits) == [hits[1], hits[0]]
    assert len(seen) == 3
    assert seen[0][0] == "原始问题"
    assert seen[0][1].startswith("文档甲\n章节甲\n")
    assert seen[1][1].startswith("文档乙\n章节乙\n")


def test_index_fingerprint_covers_chunking_and_query_format(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "rag_mode", "real")
    identities = {index_identity()}
    for key, value in [("knowledge_chunk_tokens", 180), ("knowledge_chunk_overlap", 20),
                       ("embedding_max_length", 256), ("embedding_query_style", "none")]:
        monkeypatch.setattr(s, key, value)
        identity = index_identity()
        assert identity not in identities
        identities.add(identity)


def test_real_index_identity_ignores_service_address_but_tracks_model(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, 'rag_mode', 'real')
    monkeypatch.setattr(s, 'embedding_mode', 'online')
    monkeypatch.setattr(s, 'embedding_base_url', 'http://127.0.0.1:8010/v1')
    identity = index_identity()
    monkeypatch.setattr(s, 'embedding_base_url', 'http://retrieval:8010/v1')
    assert index_identity() == identity
    monkeypatch.setattr(s, 'embedding_revision', 'different-weights')
    assert index_identity() != identity
