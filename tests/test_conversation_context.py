import json
import time
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from backend.chat_context import build_context
from backend.chat_models import ChatContextSnapshot, ChatContextTask, ChatConversation
from backend.config import get_settings
from backend.context_maintenance import enqueue, maintain_one
from backend.conversation_state import (
    StateItem,
    StateUpdate,
    active_state,
    apply_update,
    update_state,
)
from backend.db import SessionLocal
from tests.test_chat import new_chat, send, upload


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "chat_context_mode", "on")


async def drain(identifier):
    async with SessionLocal() as session:
        for _ in range(30):
            task = await session.get(ChatContextTask, identifier, populate_existing=True)
            if task.status == "done":
                return
            assert await maintain_one(session, identifier)
    raise AssertionError("maintenance did not converge")


async def seed_messages(identifier, count=50):
    async with SessionLocal() as session:
        row = await session.get(ChatConversation, identifier)
        row.messages = [{"id": f"m{i}", "request_id": f"r{i}",
            "question": "预算必须不超过十万元" if i == 0 else f"讨论话题 {i}",
            "answer": "助手猜测：内部文档秘密 CANARY", "citations": []} for i in range(count)]
        row.version += 1
        await enqueue(session, row, row.version)
        await session.commit()


async def test_durable_snapshot_summary_provenance_and_read_api(client, enabled):
    chat = await new_chat(client)
    await seed_messages(chat["id"])
    await drain(chat["id"])
    response = await client.get(f"/api/chat/conversations/{chat['id']}/context")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "done" and data["processed_turn"] == 50
    assert data["summary_until"] >= 40
    assert data["state"]["constraints"][0]["text"] == "预算必须不超过十万元"
    assert "CANARY" not in json.dumps(data, ensure_ascii=False)
    assert data["mode"] == "extractive"
    async with SessionLocal() as session:
        row = await session.get(ChatConversation, chat["id"])
        context, _, _ = await build_context(session, row, "预算是多少？", "", [])
        assert "十万元" in json.dumps(context, ensure_ascii=False)
        assert "CANARY" not in json.dumps(context, ensure_ascii=False)
        assert len(context["recent_messages"]) <= 10


async def test_document_mutation_invalidates_and_delete_cleans(client, enabled):
    chat = await new_chat(client)
    await seed_messages(chat["id"], 16)
    await drain(chat["id"])
    chat = (await client.get(f"/api/chat/conversations/{chat['id']}")).json()["data"]
    chat = await upload(client, chat)
    status = (await client.get(f"/api/chat/conversations/{chat['id']}/context")).json()["data"]
    assert status["processed_turn"] == 0 and status["state"] == {}
    assert (await client.delete(f"/api/chat/conversations/{chat['id']}")).status_code == 200
    async with SessionLocal() as session:
        assert await session.get(ChatContextSnapshot, chat["id"]) is None
        assert await session.get(ChatContextTask, chat["id"]) is None


async def test_answer_commit_enqueues_once_without_bumping_version(client, enabled):
    chat = await new_chat(client)
    response = await send(client, chat, "请用中文回答")
    assert response.status_code == 200
    chat = response.json()["data"]
    assert chat["messages"][0]["id"] == chat["messages"][0]["request_id"]
    await drain(chat["id"])
    refreshed = (await client.get(f"/api/chat/conversations/{chat['id']}")).json()["data"]
    assert refreshed["version"] == chat["version"]
    async with SessionLocal() as session:
        assert len(list(await session.scalars(select(ChatContextTask)))) == 1


async def test_failed_maintenance_retries_without_losing_messages(client, enabled, monkeypatch):
    chat = await new_chat(client)
    await seed_messages(chat["id"], 2)

    async def fail(*args):
        raise RuntimeError("private content must not enter task error")

    monkeypatch.setattr("backend.context_maintenance.update_state", fail)
    async with SessionLocal() as session:
        assert not await maintain_one(session, chat["id"])
        task = await session.get(ChatContextTask, chat["id"])
        assert task.status == "pending" and task.next_attempt > time.time()
        assert task.error_code == "context_update_failed"
        assert len((await session.get(ChatConversation, chat["id"])).messages) == 2


async def test_lease_recovery_and_duplicate_delivery(client, enabled):
    chat = await new_chat(client)
    await seed_messages(chat["id"], 2)
    async with SessionLocal() as session:
        task = await session.get(ChatContextTask, chat["id"])
        task.status, task.lease_until, task.lease_token = "running", time.time() + 100, "old"
        await session.commit()
        assert not await maintain_one(session, chat["id"])
        task.lease_until = 0
        await session.commit()
        assert await maintain_one(session, chat["id"])
        generation = (await session.get(ChatContextSnapshot, chat["id"])).generation
        assert not await maintain_one(session, chat["id"])
        assert (await session.get(ChatContextSnapshot, chat["id"])).generation == generation


async def test_unprocessed_history_cannot_silently_disappear(client, enabled, monkeypatch):
    chat = await new_chat(client)
    await seed_messages(chat["id"], 50)
    monkeypatch.setattr(get_settings(), "chat_context_tokens", 1000)
    async with SessionLocal() as session:
        row = await session.get(ChatConversation, chat["id"])
        with pytest.raises(HTTPException):
            await build_context(session, row, "继续", "", [])
        monkeypatch.setattr(get_settings(), "chat_context_mode", "shadow")
        context, _, _ = await build_context(session, row, "继续", "", [])
        assert "recent_messages" in context


def test_state_rejects_invention_and_unconfirmed_decision():
    messages = [{"id": "m1", "text": "也许使用方案甲", "turn": 1}]
    with pytest.raises(ValueError):
        apply_update({}, StateUpdate(items=[StateItem(category="decisions", source_message_id="m1", quote="确认使用甲")]), messages)
    state = apply_update({}, StateUpdate(items=[StateItem(category="decisions", source_message_id="m1", quote="也许使用方案甲")]), messages)
    assert not state["decisions"] and state["open_questions"]


async def test_long_conversation_fixed_cases():
    cases = [json.loads(line) for line in Path("evaluations/context_cases.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(cases) >= 30
    for case in cases:
        state = {}
        for turn in range(case["turns"]):
            question = case["initial"] if turn == 0 else case["correction"] if turn == 35 else case["filler"]
            state = await update_state(state, [{"id": f"m{turn}", "turn": turn + 1, "text": question}])
        active = active_state(state)
        assert any(item["text"] == case["correction"] for item in active["constraints"]), case
        assert all(item["text"] != case["initial"] for item in active["constraints"]), case
        assert all(item["source_type"] == "user_statement" for rows in active.values() for item in rows)


async def test_lazy_backfill_even_when_answer_overflows(client, enabled, monkeypatch):
    chat = await new_chat(client)
    monkeypatch.setattr(get_settings(), "chat_context_mode", "off")
    await seed_messages(chat["id"], 50)
    monkeypatch.setattr(get_settings(), "chat_context_mode", "on")
    monkeypatch.setattr(get_settings(), "chat_context_tokens", 1000)
    chat = (await client.get(f"/api/chat/conversations/{chat['id']}")).json()["data"]
    assert (await send(client, chat, "继续")).status_code == 422
    async with SessionLocal() as session:
        assert (await session.get(ChatContextTask, chat["id"])).status == "pending"
        assert len((await session.get(ChatConversation, chat["id"])).messages) == 50


async def test_private_context_and_revoked_project(client, platform_client, enabled):
    from tests.test_api import project, role_client
    p = await project(client)
    chat = await new_chat(client, p["id"])
    await seed_messages(chat["id"], 2)
    await drain(chat["id"])
    member = await role_client(client, p["id"], "viewer", "context-other@example.com")
    try:
        assert (await member.get(f"/api/chat/conversations/{chat['id']}/context")).status_code == 404
        assert (await platform_client.get(f"/api/chat/conversations/{chat['id']}/context")).status_code == 403
        from uuid import uuid4
        assert (await client.delete(f"/api/projects/{p['id']}", headers={"Idempotency-Key": str(uuid4())})).status_code == 200
        assert (await client.get(f"/api/chat/conversations/{chat['id']}/context")).status_code == 404
    finally:
        await member.aclose()


async def test_state_completion_cannot_publish_changed_sources(client, enabled, monkeypatch):
    chat = await new_chat(client)
    await seed_messages(chat["id"], 2)
    original = update_state

    async def change_source(previous, messages):
        async with SessionLocal() as other:
            row = await other.get(ChatConversation, chat["id"])
            row.documents = [{"id": "new", "text": "changed"}]
            row.version += 1
            await enqueue(other, row, row.version)
            await other.commit()
        return await original(previous, messages)

    monkeypatch.setattr("backend.context_maintenance.update_state", change_source)
    async with SessionLocal() as session:
        assert not await maintain_one(session, chat["id"])
        assert await session.get(ChatContextSnapshot, chat["id"]) is None
        assert (await session.get(ChatContextTask, chat["id"])).status == "pending"


async def test_real_state_and_summary_validate_sources(monkeypatch):
    from backend.conversation_state import SummaryItem, SummaryUpdate, summarize
    monkeypatch.setattr(get_settings(), "model_mode", "real")

    async def fake(task, source, schema):
        assert "assistant" not in str(source)
        if schema is StateUpdate:
            return StateUpdate(items=[StateItem(category="constraints", source_message_id="u1", quote="必须用中文")])
        return SummaryUpdate(items=[SummaryItem(text="用户要求中文", source_message_ids=["not-a-source"])])

    monkeypatch.setattr("backend.intelligence.structured", fake)
    messages = [{"id": "u1", "turn": 1, "text": "必须用中文"}]
    state = await update_state({}, messages)
    assert state["constraints"][0]["source_message_id"] == "u1"
    summary = await summarize([], messages, state)
    assert summary == [{"text": "必须用中文", "source_message_ids": ["u1"], "source_type": "user_statement"}]


async def test_model_validation_failure_uses_provenance_bound_extracts(monkeypatch):
    from backend.conversation_state import EXTRACTIVE_FALLBACK

    monkeypatch.setattr(get_settings(), "model_mode", "real")

    async def invalid(*_):
        raise HTTPException(422, "invalid structured output")

    monkeypatch.setattr("backend.intelligence.structured", invalid)
    state = await update_state({}, [{"id": "u1", "turn": 1, "text": "必须用中文"}])
    assert state["constraints"][0]["source_message_id"] == "u1"
    assert EXTRACTIVE_FALLBACK.get() is True


@pytest.mark.parametrize("quote", ["不确定是否采用甲", "不要确认甲", "建议采用甲", "如果决定采用甲"])
def test_negated_or_hypothetical_decisions_stay_unconfirmed(quote):
    state = apply_update({}, StateUpdate(items=[StateItem(category="decisions", source_message_id="u", quote=quote)]),
        [{"id": "u", "text": quote, "turn": 1}])
    assert not state["decisions"] and state["open_questions"]


async def test_enqueue_preserves_live_lease_and_newest_target(client, enabled):
    chat = await new_chat(client)
    await seed_messages(chat["id"], 2)
    async with SessionLocal() as session:
        task = await session.get(ChatContextTask, chat["id"])
        task.status, task.lease_token, task.lease_until = "running", "active-worker", time.time() + 60
        await session.commit()
        row = await session.get(ChatConversation, chat["id"])
        await enqueue(session, row, 10)
        await enqueue(session, row, 9)
        await session.commit()
        await session.refresh(task)
        assert task.target_version == 10
        assert task.status == "running" and task.lease_token == "active-worker"


async def test_message_arriving_during_generation_is_not_skipped(client, enabled, monkeypatch):
    chat = await new_chat(client)
    await seed_messages(chat["id"], 2)
    original = update_state

    async def append_message(previous, messages):
        async with SessionLocal() as other:
            row = await other.get(ChatConversation, chat["id"])
            row.messages = [*row.messages, {"id": "m2", "request_id": "r2", "question": "必须保留新增约束", "answer": "", "citations": []}]
            row.version += 1
            await enqueue(other, row, row.version)
            await other.commit()
        return await original(previous, messages)

    monkeypatch.setattr("backend.context_maintenance.update_state", append_message)
    async with SessionLocal() as session:
        assert await maintain_one(session, chat["id"])
        assert (await session.get(ChatContextSnapshot, chat["id"])).processed_turn == 2
        assert (await session.get(ChatContextTask, chat["id"])).status == "pending"
    monkeypatch.setattr("backend.context_maintenance.update_state", original)
    await drain(chat["id"])
    response = (await client.get(f"/api/chat/conversations/{chat['id']}/context")).json()["data"]
    assert response["processed_turn"] == 3
    assert any(r["text"] == "必须保留新增约束" for r in response["state"]["constraints"])
