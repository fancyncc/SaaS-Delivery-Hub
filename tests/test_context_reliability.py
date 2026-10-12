"""Regression cases for correction, bounded state and durable maintenance."""
import json
import time
from uuid import uuid4

import pytest
from sqlalchemy import select

from backend.chat_context import build_context
from backend.chat_history import hydrate, index_history, maintain_history_indexes, persist
from backend.chat_models import (
    ChatContextSnapshot,
    ChatContextTask,
    ChatConversation,
    ChatHistoryMigration,
    ChatMessage,
)
from backend.config import get_settings
from backend.conversation_state import (
    StateItem,
    StateUpdate,
    active_state,
    apply_update,
    digest,
    source_digest,
    update_state,
)
from backend.db import SessionLocal
from tests.test_chat import new_chat, send
from tests.test_conversation_context import drain


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for name, value in {"model_mode": "deterministic", "rag_mode": "mock", "chat_context_mode": "on",
        "chat_history_enabled": True, "chat_memory_items_enabled": True, "chat_memory_candidates_enabled": True}.items():
        monkeypatch.setattr(get_settings(), name, value)


async def test_cancel_complete_and_unrelated_targets():
    state = await update_state({}, [{"id": "u1", "turn": 1, "text": "必须使用中文回答"}])
    before = json.dumps(state, ensure_ascii=False)
    cancelled = await update_state(state, [{"id": "u2", "turn": 2, "text": "取消之前必须使用中文回答的要求"}])
    assert not active_state(cancelled)["constraints"]
    assert json.dumps(state, ensure_ascii=False) == before
    unrelated = apply_update(state, StateUpdate(items=[StateItem(category="constraints", source_message_id="u3",
        quote="取消导入任务", operation="revoke", supersedes=[state["constraints"][0]["id"]])]),
        [{"id": "u3", "turn": 3, "text": "取消导入任务"}])
    assert active_state(unrelated)["constraints"] and unrelated["open_questions"]
    goals = await update_state({}, [{"id": "g1", "turn": 1, "text": "我想完成员工培训"}])
    goals = await update_state(goals, [{"id": "g2", "turn": 2, "text": "已完成我想完成员工培训这个目标"}])
    assert not active_state(goals)["goals"]


@pytest.mark.parametrize("prefix,suffix", [("不要", ""), ("是否", "？"), ("如果", "")])
async def test_nonaffirmative_corrections_preserve_active_state_even_with_trimmed_model_quote(prefix, suffix):
    state = await update_state({}, [{"id": "u1", "turn": 1, "text": "必须使用中文回答"}])
    quote = "取消之前必须使用中文回答的要求"
    message = {"id": "u2", "turn": 2, "text": prefix + quote + suffix}
    updated = apply_update(state, StateUpdate(items=[StateItem(category="constraints", source_message_id="u2",
        quote=quote, operation="revoke", supersedes=[state["constraints"][0]["id"]])]), [message])
    assert active_state(updated)["constraints"] and updated["open_questions"]
    updated = await update_state(state, [message])
    assert active_state(updated)["constraints"]


async def test_large_state_selects_relevant_entries_and_current_retraction(client):
    chat = await new_chat(client)
    messages, state = [], {}
    for i in range(130):
        question = f"第{i}项约束必须保留对应验收条件并核对执行凭证" if i < 120 else "讨论其他事项"
        messages.append({"id": f"m{i}", "request_id": str(uuid4()), "question": question, "answer": "", "citations": []})
        state = await update_state(state, [{"id": f"m{i}", "turn": i + 1, "text": question}])
    async with SessionLocal() as session:
        row = await session.get(ChatConversation, chat["id"])
        row.messages = messages
        snapshot = ChatContextSnapshot(conversation_id=row.id, tenant_id=row.tenant_id, user_id=row.user_id,
            state=state, summary=[], processed_turn=130, summary_until=122,
            source_digest=source_digest(messages), documents_digest=digest(row.documents), producer_version="conversation-context")
        session.add(snapshot)
        await session.commit()
        context, _, _ = await build_context(session, row, "第3项验收条件是什么", "", [])
        assert any(r["text"].startswith("第3项") for r in context["state"]["constraints"])
        assert len(context["state"]["constraints"]) < 120
        assert len(snapshot.state["constraints"]) == 120
        context, _, _ = await build_context(session, row, "取消之前第3项约束必须保留对应验收条件并核对执行凭证的要求", "", [])
        assert all(not r["text"].startswith("第3项") for r in context["state"].get("constraints", []))


@pytest.mark.parametrize("source_turn", [1, 2, 10])
async def test_maintained_retractions_do_not_return_through_recent_or_recall(client, source_turn):
    chat = await new_chat(client)
    messages = [{"id": f"m{i}", "request_id": str(uuid4()), "question": "讨论其他事项",
        "answer": "", "citations": []} for i in range(12)]
    messages[source_turn - 1]["question"] = "必须使用中文回答 ARCHIVED-CONSTRAINT"
    messages[source_turn]["question"] = "取消之前必须使用中文回答 ARCHIVED-CONSTRAINT 的要求"
    if source_turn > 1:
        messages[source_turn - 2]["question"] = "ARCHIVED-CONSTRAINT 的旧讨论"
    state = {}
    for index, message in enumerate(messages):
        state = await update_state(state, [{"id": message["id"], "turn": index + 1, "text": message["question"]}])
    async with SessionLocal() as session:
        row = await session.get(ChatConversation, chat["id"])
        row.messages = messages
        session.add(ChatContextSnapshot(conversation_id=row.id, tenant_id=row.tenant_id, user_id=row.user_id,
            state=state, summary=[], processed_turn=12, summary_until=4, source_digest=source_digest(messages),
            documents_digest=digest(row.documents), producer_version="conversation-context"))
        await session.commit()
        context, _, _ = await build_context(session, row, "ARCHIVED-CONSTRAINT 是什么", "", [])
        represented = context["recent_messages"] + context["historical_user_statements"]
        assert all(item.get("message_id") != messages[source_turn - 1]["id"] for item in represented)
        assert all(item.get("adjacent") != messages[source_turn - 1]["question"] for item in represented)
        assert not context["state"].get("constraints")


async def test_health_retry_preserves_conversation_version(client, platform_client):
    chat = (await send(client, await new_chat(client), "目标是完成培训")).json()["data"]
    async with SessionLocal() as session:
        task = await session.get(ChatContextTask, chat["id"])
        task.queued_at = time.time() - 100
        await session.commit()
    path = f"/api/chat/conversations/{chat['id']}/context"
    health = (await client.get(path)).json()["data"]
    assert health["health"] == "unavailable" and health["pending_turns"] == 1
    assert (await platform_client.get(path)).status_code == 403
    async with SessionLocal() as session:
        task = await session.get(ChatContextTask, chat["id"])
        task.status, task.attempts, task.error_code = "failed", 5, "context_update_failed"
        await session.commit()
    response = await client.post(path + "/retry", json={"expected_version": chat["version"]})
    assert response.status_code == 200 and response.json()["data"]["health"] == "waiting"
    assert (await client.get(f"/api/chat/conversations/{chat['id']}")).json()["data"]["version"] == chat["version"]
    await drain(chat["id"])
    assert (await client.get(path)).json()["data"]["health"] == "ready"


async def test_metadata_changes_do_not_leave_completed_maintenance_pending(client):
    chat = (await send(client, await new_chat(client), "必须保存验收凭证")).json()["data"]
    renamed = await client.patch(f"/api/chat/conversations/{chat['id']}",
        json={"expected_version": chat["version"], "title": "实施记录"})
    assert renamed.status_code == 200
    await drain(chat["id"])
    health = (await client.get(f"/api/chat/conversations/{chat['id']}/context")).json()["data"]
    assert health["status"] == "done" and health["health"] == "ready"
    listing = (await client.get("/api/chat/conversations")).json()["data"]["conversations"]
    assert listing[0]["message_count"] == 1 and listing[0]["preview"] == "必须保存验收凭证"


async def test_preference_dimensions_coexist_but_language_change_conflicts(client):
    await client.put('/api/chat/memory-preferences', json={"expected_version": 0, "auto_extract": True})
    chat = (await send(client, await new_chat(client), "请记住：以后请用中文；以后请先说结论")).json()["data"]
    await drain(chat["id"])
    candidates = (await client.get('/api/chat/memory-candidates')).json()["data"]
    assert {r["key"] for r in candidates} == {"preference.language", "preference.answer_order"}
    for candidate in candidates:
        response = await client.post(f"/api/chat/memory-candidates/{candidate['id']}/accept", json={"expected_version": 1})
        assert response.status_code == 200
    assert len((await client.get('/api/chat/memory-items')).json()["data"]) == 2
    chat = (await send(client, chat, "以后请用英文")).json()["data"]
    await drain(chat["id"])
    candidate = (await client.get('/api/chat/memory-candidates')).json()["data"][0]
    assert candidate["key"] == "preference.language" and len(candidate["conflicts"]) == 1
    assert (await client.post(f"/api/chat/memory-candidates/{candidate['id']}/accept", json={"expected_version": 1})).status_code == 409


async def test_history_index_retries_without_new_messages_and_rebuilds_identity(client, monkeypatch):
    chat = await new_chat(client)
    async with SessionLocal() as session:
        row = await session.get(ChatConversation, chat["id"])
        await persist(session, row, [{"request_id": str(uuid4()), "question": f"历史{i}", "answer": "", "citations": []} for i in range(10)])
        await session.commit()
        await hydrate(session, row)
        tenant = row.tenant_id
        monkeypatch.setattr(get_settings(), "rag_mode", "real")
        async def unavailable(*args, **kwargs):
            raise RuntimeError("temporary dependency failure")
        monkeypatch.setattr("backend.retrieval_models.embeddings", unavailable)
        assert await index_history(session, row) is False
        marker = await session.get(ChatHistoryMigration, chat["id"], populate_existing=True)
        assert marker.index_status == "retrying" and marker.index_next_attempt > time.time()
        marker.index_next_attempt = 0
        await session.commit()
        calls = []
        async def vectors(texts, **kwargs):
            calls.append(len(texts))
            return [[1., 0.] for _ in texts]
        monkeypatch.setattr("backend.retrieval_models.embeddings", vectors)
        await maintain_history_indexes(session, tenant)
        await maintain_history_indexes(session, tenant)
        assert sum(calls) == 10
        marker = await session.get(ChatHistoryMigration, chat["id"], populate_existing=True)
        assert marker.index_status == "done" and marker.index_attempts == 0
        monkeypatch.setattr(get_settings(), "embedding_revision", "changed-identity")
        await maintain_history_indexes(session, tenant)
        await maintain_history_indexes(session, tenant)
        assert sum(calls) == 20
        assert len(list(await session.scalars(select(ChatMessage).where(ChatMessage.role == "user")))) == 10


@pytest.mark.parametrize("context_enabled", [False, True])
async def test_maintenance_dispatches_independent_history_jobs(client, monkeypatch, context_enabled):
    from backend.context_maintenance import enqueue, maintain_pending
    monkeypatch.setattr(get_settings(), "chat_context_mode", "on" if context_enabled else "off")
    chats = [await new_chat(client), await new_chat(client)]
    async with SessionLocal() as session:
        for chat in chats:
            row = await session.get(ChatConversation, chat["id"])
            await persist(session, row, [{"request_id": str(uuid4()), "question": "必须保存验收凭证",
                "answer": "已记录", "citations": []}])
            await enqueue(session, row, row.version)
        await session.commit()
    monkeypatch.setattr(get_settings(), "rag_mode", "real")
    async def vectors(texts, **kwargs):
        return [[1., 0.] for _ in texts]
    monkeypatch.setattr("backend.retrieval_models.embeddings", vectors)
    # Two dispatcher instances can discover the same jobs; DB leases fence writes.
    import asyncio
    await asyncio.gather(maintain_pending(), maintain_pending())
    await maintain_pending()
    async with SessionLocal() as session:
        for chat in chats:
            marker = await session.get(ChatHistoryMigration, chat["id"])
            assert marker.index_status == "done" and marker.index_attempts == 0
            task = await session.get(ChatContextTask, chat["id"])
            snapshot = await session.get(ChatContextSnapshot, chat["id"])
            if context_enabled:
                assert task.status == "done" and task.consumer_seen_at > 0
                assert snapshot.processed_turn == 1
            else:
                assert task is None and snapshot is None


@pytest.mark.parametrize("stream", [False, True])
async def test_both_transports_share_clarification_and_skip_tools(client, monkeypatch, stream):
    from backend.chat_planning import QueryPlan
    async def clarify(*args, **kwargs):
        return QueryPlan(action="clarify", query="", clarification="你指哪个项目？")
    async def forbidden(*args, **kwargs):
        raise AssertionError("clarification must not search")
        yield
    monkeypatch.setattr("backend.chat_pipeline.plan_query", clarify)
    monkeypatch.setattr("backend.chat_tools.tool_context", forbidden)
    chat = await new_chat(client)
    response = await send(client, chat, "它怎么办", stream=stream)
    assert response.status_code == 200
    saved = (await client.get(f"/api/chat/conversations/{chat['id']}")).json()["data"]
    assert saved["messages"][0]["mode"] == "clarification"
    assert saved["messages"][0]["answer"] == "你指哪个项目？"
