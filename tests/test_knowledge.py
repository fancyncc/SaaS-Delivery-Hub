import pytest
from sqlalchemy import select

from backend.agent_models import KnowledgePiece
from backend.config import get_settings
from backend.db import SessionLocal
from backend.knowledge import chunk_text, index_document, lexemes, process_pending, retrieve
from backend.models import KnowledgeDocument, Tenant


def test_chunking_preserves_headings_steps_and_identifiers():
    pieces = chunk_text("# 成员导入\n\n" + "步骤 1 校验 owner_email 字段，处理 ERR_DUPLICATE。" * 80)
    assert len(pieces) > 1
    assert all(heading == "成员导入" for heading, _ in pieces)
    assert "owner_email" in lexemes(pieces[0][1])
    assert "err_duplicate" in lexemes(pieces[0][1])
    with pytest.raises(ValueError):
        chunk_text("x", size=80, overlap=80)


async def test_pending_new_version_cannot_fall_back_to_old_index(client, monkeypatch):
    payload = dict(title="成员导入", version=1, module="import", source="原创测试", license="仅用于测试", body="成员导入必须先校验邮箱、部门和角色，再提交审批。")
    assert (await client.post("/api/knowledge", json=payload)).status_code == 200
    monkeypatch.setattr(get_settings(), "execution_mode", "worker")
    response = await client.post("/api/knowledge", json={**payload, "version": 2})
    async with SessionLocal() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == "legacy-demo"))
        assert await retrieve(session, tenant.id, "成员导入") == []
        await process_pending(session, tenant.id)
        await session.commit()
        hits = await retrieve(session, tenant.id, "成员导入")
        assert hits and all(h["version"] == 2 for h in hits)
        assert hits[0]["document_id"] == response.json()["data"]["id"]
        assert {"heading", "ordinal", "trace_id", "index_version"} <= hits[0].keys()


async def test_index_failure_not_published_and_rebuild_is_atomic(monkeypatch):
    import backend.knowledge as knowledge
    async with SessionLocal() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == "legacy-demo"))
        doc = KnowledgeDocument(tenant_id=tenant.id, title="索引失败", version=1, module="import", source="原创", license="测试", body="成员导入" * 200)
        session.add(doc)
        await session.flush()
        async def unavailable(value):
            raise ValueError("do not persist token=secret")
        monkeypatch.setattr(knowledge, "embed", unavailable)
        await process_pending(session, tenant.id)
        assert doc.index_status == "failed" and doc.index_error == "ValueError"
        assert list(await session.scalars(select(KnowledgePiece))) == []
        assert await retrieve(session, tenant.id, "成员导入") == []
        async def offline(value):
            return None
        monkeypatch.setattr(knowledge, "embed", offline)
        await process_pending(session, tenant.id)
        assert doc.index_status == "ready"
        count = len(list(await session.scalars(select(KnowledgePiece))))
        await index_document(session, doc)
        assert len(list(await session.scalars(select(KnowledgePiece)))) == count


async def test_model_change_requires_new_index(client, monkeypatch):
    payload = dict(title="成员导入", version=1, module="import", source="原创测试", license="仅用于测试", body="成员导入必须先校验邮箱、部门和角色，再提交审批。")
    response = await client.post("/api/knowledge", json=payload)
    monkeypatch.setattr(get_settings(), "knowledge_index_version", "chunks-v2")
    async with SessionLocal() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == "legacy-demo"))
        assert await retrieve(session, tenant.id, "成员导入") == []
    assert (await client.post(f"/api/knowledge/{response.json()['data']['id']}/reindex")).status_code == 200
    async with SessionLocal() as session:
        assert await retrieve(session, tenant.id, "成员导入")


async def test_labelled_retrieval_benchmark(tmp_path):
    import json

    from backend.rag import CORPUS
    from backend.rag_evaluation import evaluate_retrieval
    async with SessionLocal() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == "legacy-demo"))
        for item in CORPUS:
            doc = KnowledgeDocument(tenant_id=tenant.id, title=item.title, version=1, module=item.module, source="原创演示", license="测试", body=item.text)
            session.add(doc)
            await session.flush()
            await index_document(session, doc)
        result = await evaluate_retrieval(session, tenant.id)
        assert result["dataset_size"] >= 50
        assert result["human_reviewed"] == 0  # Candidate labels must not claim human sign-off.
        assert result["recall_at_5"] >= .70
        assert result["no_answer_accuracy"] == 1
        (tmp_path / "rag-report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print({k: v for k, v in result.items() if k != "cases"})


def test_business_expansion_keeps_identifiers_and_does_not_match_substrings():
    from backend.knowledge import expand_query
    value = "SSO v2.1 报错 ERR_AUTH，字段 owner_email"
    assert expand_query(value).startswith(value)
    assert "单点登录" in expand_query(value)
    assert expand_query("password 字段") == "password 字段"


def test_tokenizer_chunk_budget_and_repeated_table_headers():
    class Tokenizer:
        def __call__(self, value, **kwargs):
            return {"offset_mapping": [(i, i + 1) for i in range(len(value))]}
    body = "# 字段\n\n| name | role |\n| --- | --- |\n" + "\n".join(
        f"| user_{i} | member |" for i in range(20))
    chunks = chunk_text(body, size=100, overlap=10, tokenizer=Tokenizer())
    assert all(len(text) <= 100 for _, text in chunks)
    tables = [text for _, text in chunks if "user_" in text]
    assert len(tables) > 1
    assert all(text.startswith("| name | role |\n| --- | --- |") for text in tables)
    assert all(f"user_{i}" in "\n".join(tables) for i in range(20))


async def test_real_local_upload_is_queued_without_loading_models(client, monkeypatch):
    import backend.knowledge_routes as routes
    monkeypatch.setattr(get_settings(), "rag_mode", "real")
    monkeypatch.setattr(get_settings(), "embedding_mode", "local")
    monkeypatch.setattr(get_settings(), "embedding_model", "")
    monkeypatch.setattr(get_settings(), "execution_mode", "inline")
    async def unexpected(*args):
        pytest.fail("upload must not load the model")
    monkeypatch.setattr(routes, "index_document", unexpected)
    response = await client.post("/api/knowledge", json=dict(title="CPU 排队测试", version=1,
        module="import", source="原创测试", license="内部测试", body="成员导入必须先校验邮箱、部门和角色，再提交审批。"))
    assert response.status_code == 200, response.text
    async with SessionLocal() as session:
        doc = await session.get(KnowledgeDocument, response.json()["data"]["id"])
        assert doc.index_status == "pending"
        assert not list(await session.scalars(select(KnowledgePiece)))
