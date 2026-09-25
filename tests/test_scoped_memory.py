import importlib.util
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, select

from backend.chat_history import hydrate, persist, recall_history
from backend.chat_models import (
    ChatConversation,
    ChatHistoryMigration,
    ChatMemory,
    ChatMessage,
    MemoryCandidate,
    MemoryItem,
)
from backend.config import get_settings
from backend.db import SessionLocal
from backend.memory_items import effective_memory
from backend.models import Base
from tests.test_api import project
from tests.test_chat import new_chat, send
from tests.test_conversation_context import drain


@pytest.fixture(autouse=True)
def features(monkeypatch):
    for name in ("chat_history_enabled", "chat_memory_items_enabled", "chat_memory_candidates_enabled"):
        monkeypatch.setattr(get_settings(), name, True)
    monkeypatch.setattr(get_settings(), "chat_context_mode", "on")
    monkeypatch.setattr(get_settings(), "model_mode", "deterministic")
    monkeypatch.setattr(get_settings(), "rag_mode", "mock")


async def test_normalized_cutover_pagination_and_2000_turn_limit(client):
    chat = await new_chat(client)
    messages = [{"request_id": str(uuid4()), "question": f"历史主题 {i}", "answer": f"答复 {i}", "citations": []} for i in range(2000)]
    messages[30]["question"] = "海豚迁移方案特征码 HISTORY-UNIQUE"
    async with SessionLocal() as session:
        row = await session.get(ChatConversation, chat["id"])
        await persist(session, row, messages)
        await session.commit()
        assert (await session.get(ChatHistoryMigration, row.id)).activated
        assert len(list(await session.scalars(select(ChatMessage)))) == 4000
        await hydrate(session, row)
        hits = await recall_history(session, row, "海豚迁移方案 HISTORY-UNIQUE")
        assert any(h["turn"] == 31 for h in hits) and len(hits) <= 6
        assert all(h["kind"] == "conversation_history" for h in hits)
    data = (await client.get(f"/api/chat/conversations/{chat['id']}")).json()["data"]
    assert len(data["messages"]) == 50 and data["message_count"] == 2000
    page = (await client.get(f"/api/chat/conversations/{chat['id']}/messages", params={"before": data["next_before"]})).json()["data"]
    assert page["messages"][-1]["question"] == "历史主题 1949"
    assert (await send(client, data, "继续")).status_code == 422


async def test_new_writes_freeze_json_and_rollback_retains_messages(client, monkeypatch):
    chat = await new_chat(client)
    chat = (await send(client, chat, "请用中文回答")).json()["data"]
    async with SessionLocal() as session:
        row = await session.get(ChatConversation, chat["id"])
        assert row.messages == []
    monkeypatch.setattr(get_settings(), "chat_history_enabled", False)
    chat = (await send(client, chat, "现在继续")).json()["data"]
    assert len(chat["messages"]) == 2
    async with SessionLocal() as session:
        row = await session.get(ChatConversation, chat["id"])
        assert row.messages == []
        assert len((await hydrate(session, row)).messages) == 2


async def test_scope_precedence_expiry_legacy_and_versions(client):
    p = await project(client)
    chat = await new_chat(client, p["id"])
    for scope, scope_id, content in (("user", None, "通用偏好"), ("workspace", None, "空间偏好"),
        ("project", p["id"], "项目偏好"), ("conversation", chat["id"], "对话偏好")):
        result = await client.post('/api/chat/memory-items', json={"scope": scope, "scope_id": scope_id,
            "category": "preference", "key": "reply", "content": content})
        assert result.status_code == 200, result.text
    assert (await client.post('/api/chat/memory-items', json={"scope": "user", "category": "background", "key": "identity", "content": "私有背景"})).status_code == 422
    assert (await client.put('/api/chat/memory', json={"content": "旧偏好", "expected_version": 0})).status_code == 200
    async with SessionLocal() as session:
        row = await session.get(ChatConversation, chat["id"])
        user = SimpleNamespace(user_id=row.user_id, tenant_id=row.tenant_id)
        effective = await effective_memory(session, user, row, "旧偏好")
        assert "对话偏好" in effective and "项目偏好" not in effective and "旧偏好" in effective
        other = SimpleNamespace(id="other", project_id=None)
        assert "空间偏好" in await effective_memory(session, user, other, "")
        items = list(await session.scalars(select(MemoryItem).where(MemoryItem.scope == "conversation")))
        item = items[0]
        identifier, version = item.id, item.version
    assert (await client.patch(f'/api/chat/memory-items/{identifier}', json={"expected_version": version + 1, "content": "stale"})).status_code == 409
    assert (await client.patch(f'/api/chat/memory-items/{identifier}', json={"expected_version": version, "content": "expired", "expires_at": time.time()-1})).status_code == 200
    async with SessionLocal() as session:
        row = await session.get(ChatConversation, chat["id"])
        assert "项目偏好" in await effective_memory(session, user, row, "旧偏好")


async def test_candidates_opt_in_conflict_confirmation_and_rejection(client):
    assert not (await client.get('/api/chat/memory-preferences')).json()["data"]["auto_extract"]
    chat = (await send(client, await new_chat(client), "请记住：以后请用中文")).json()["data"]
    await drain(chat["id"])
    assert (await client.get('/api/chat/memory-candidates')).json()["data"] == []
    assert (await client.put('/api/chat/memory-preferences', json={"expected_version": 0, "auto_extract": True})).status_code == 200
    chat = (await send(client, chat, "请记住：以后请用英文")).json()["data"]
    await drain(chat["id"])
    candidate = (await client.get('/api/chat/memory-candidates')).json()["data"][0]
    assert not (await client.get('/api/chat/memory-items')).json()["data"]
    existing = (await client.post('/api/chat/memory-items', json={"key": "explicit_preference", "content": "旧内容"})).json()["data"]
    path = f"/api/chat/memory-candidates/{candidate['id']}/accept"
    assert (await client.post(path, json={"expected_version": candidate["version"]})).status_code == 409
    assert (await client.post(path, json={"expected_version": candidate["version"], "replace_id": existing["id"], "replace_version": existing["version"]})).status_code == 200
    assert (await client.post(path, json={"expected_version": candidate["version"]})).status_code == 409
    chat = (await send(client, chat, "我是实施负责人")).json()["data"]
    await drain(chat["id"])
    rejected = (await client.get('/api/chat/memory-candidates')).json()["data"][0]
    assert (await client.post(f"/api/chat/memory-candidates/{rejected['id']}/reject", json={"expected_version": rejected["version"]})).status_code == 200
    chat = (await send(client, chat, "我是实施负责人")).json()["data"]
    await drain(chat["id"])
    assert (await client.get('/api/chat/memory-candidates')).json()["data"] == []


async def test_candidate_source_deletion_and_no_cross_conversation_recall(client):
    await client.put('/api/chat/memory-preferences', json={"expected_version": 0, "auto_extract": True})
    chat = (await send(client, await new_chat(client), "请记住：偏好简洁输出")).json()["data"]
    await drain(chat["id"])
    candidate = (await client.get('/api/chat/memory-candidates')).json()["data"][0]
    other = await new_chat(client)
    async with SessionLocal() as session:
        row = await session.get(ChatConversation, other["id"])
        assert await recall_history(session, row, "偏好简洁输出") == []
    assert (await client.delete(f"/api/chat/conversations/{chat['id']}")).status_code == 200
    assert (await client.post(f"/api/chat/memory-candidates/{candidate['id']}/accept", json={"expected_version": candidate["version"]})).status_code == 404
    async with SessionLocal() as session:
        assert not list(await session.scalars(select(MemoryCandidate)))


def test_scoped_memory_migration_preserves_messages_and_legacy(monkeypatch):
    spec = importlib.util.spec_from_file_location("scoped_migration", "alembic/versions/0027_scoped_memory.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        Base.metadata.create_all(connection)
        original = {"request_id": "request-one", "question": "问题", "answer": "答案", "citations": [{"id": "source", "text": "证据"}]}
        connection.execute(ChatConversation.__table__.insert().values(id="c", tenant_id="t", user_id="u", messages=[original]))
        connection.execute(ChatMemory.__table__.insert().values(id="m", tenant_id="t", user_id="u", content="原始偏好", version=7))
        monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(connection)))
        module.upgrade()
        rows = list(connection.execute(select(ChatMessage.__table__)).mappings())
        assert len(rows) == 2 and {r["content"] for r in rows} == {"问题", "答案"}
        user = next(r for r in rows if r["role"] == "user")
        assert user["metadata_json"]["citations"] == original["citations"]
        memory = connection.execute(select(MemoryItem.__table__)).mappings().one()
        assert memory["content"] == "原始偏好" and memory["scope"] == "workspace" and memory["version"] == 7
    engine.dispose()


async def test_agent_lessons_require_registered_durable_validation(client, monkeypatch):
    from backend.agent_lessons import WORKFLOW_VERSION
    from backend.agent_models import AgentLesson
    monkeypatch.setattr(get_settings(), "agent_engine", "v2")
    monkeypatch.setattr(get_settings(), "agent_experience_enabled", True)
    p = await project(client)
    response = await client.post(f"/api/projects/{p['id']}/runs", headers={"Idempotency-Key": str(uuid4())})
    assert response.status_code == 200, response.text
    async with SessionLocal() as session:
        lessons = list(await session.scalars(select(AgentLesson)))
        assert any(r.category == "workflow" and r.verified for r in lessons)
        assert all(r.workflow_version == WORKFLOW_VERSION for r in lessons)
        assert all(r.evidence.get("action_ids") for r in lessons if r.verified)
        assert any(not r.verified for r in lessons)


async def test_history_hybrid_and_embedding_failure(client, monkeypatch):
    from backend.chat_history import embedding_identity, index_history
    chat = await new_chat(client)
    messages = [{"request_id": str(uuid4()), "question": f"主题{i}", "answer": "", "citations": []} for i in range(20)]
    messages[0]["question"] = "语义目标与关键词不同"
    messages[1]["question"] = "关键词回退目标"
    async with SessionLocal() as session:
        row = await session.get(ChatConversation, chat["id"])
        await persist(session, row, messages)
        await session.commit()
        await hydrate(session, row)
        monkeypatch.setattr(get_settings(), "rag_mode", "real")
        async def vectors(texts, **kwargs):
            return [[1., 0.] if kwargs.get("query") or text == messages[0]["question"] else [0., 1.] for text in texts]
        monkeypatch.setattr("backend.retrieval_models.embeddings", vectors)
        await index_history(session, row)
        await session.commit()
        result = await recall_history(session, row, "没有共同词的检索问句")
        assert result[0]["turn"] == 1
        async def failed(*args, **kwargs):
            raise RuntimeError("offline")
        monkeypatch.setattr("backend.retrieval_models.embeddings", failed)
        assert any(h["turn"] == 2 for h in await recall_history(session, row, "关键词回退目标"))
        stored = list(await session.scalars(select(ChatMessage).where(ChatMessage.role == "user")))
        assert any(m.embedding_identity == embedding_identity() for m in stored)


async def test_memory_and_pages_are_private(client):
    from tests.test_api import role_client
    p = await project(client)
    chat = (await send(client, await new_chat(client, p["id"]), "私有内容")).json()["data"]
    item = (await client.post('/api/chat/memory-items', json={"scope": "project", "scope_id": p["id"], "key": "私有", "content": "不能共享"})).json()["data"]
    other = await role_client(client, p["id"], "viewer", "memory-private@example.com")
    try:
        assert (await other.get('/api/chat/memory-items')).json()["data"] == []
        assert (await other.get(f"/api/chat/conversations/{chat['id']}/messages")).status_code == 404
        assert (await other.patch(f"/api/chat/memory-items/{item['id']}", json={"expected_version": 1, "content": "篡改"})).status_code == 404
    finally:
        await other.aclose()
