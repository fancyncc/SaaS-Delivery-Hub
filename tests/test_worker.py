import uuid

import pytest
from sqlalchemy import select
from test_api import project


async def test_worker_recovers_from_committed_node_without_repeating(client, monkeypatch):
    from backend import worker
    settings = get_settings()
    monkeypatch.setattr(settings, "execution_mode", "worker")
    p = await project(client)
    response = await client.post(f"/api/projects/{p['id']}/runs", headers={"Idempotency-Key": str(uuid.uuid4())})
    run_id = response.json()["data"]["id"]
    async with SessionLocal() as session:
        event_id = (await session.scalar(select(WorkflowOutbox).where(WorkflowOutbox.run_id == run_id))).id
    original = worker.advance
    calls = 0
    async def fail_after_first(session, run, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ConnectionError("injected transient dependency failure")
        return await original(session, run, **kwargs)
    monkeypatch.setattr(worker, "advance", fail_after_first)
    await process_event(event_id)
    async with SessionLocal() as session:
        event = await session.get(WorkflowOutbox, event_id)
        assert event.attempts == 1 and not event.processed
        steps = (await session.scalars(select(AgentStep).where(AgentStep.run_id == run_id))).all()
        assert len(steps) == 2 and {s.node for s in steps} == {"create_project", "agent_evaluation"}
    monkeypatch.setattr(worker, "advance", original)
    await process_event(event_id, raise_errors=True)
    async with SessionLocal() as session:
        assert (await session.get(WorkflowOutbox, event_id)).processed
        assert len((await session.scalars(select(AgentStep).where(AgentStep.run_id == run_id))).all()) == 11

from backend.config import get_settings
from backend.db import SessionLocal
from backend.models import AgentRun, AgentStep, WorkflowOutbox
from backend.worker import process_event


async def test_outbox_advances_and_replay_does_not_duplicate_steps(client):
    settings = get_settings()
    original = settings.execution_mode
    settings.execution_mode = "worker"
    try:
        p = await project(client)
        started = await client.post(f"/api/projects/{p['id']}/runs", headers={"Idempotency-Key": str(uuid.uuid4())})
        assert started.status_code == 200
        run_id = started.json()["data"]["id"]
        async with SessionLocal() as session:
            event = await session.scalar(select(WorkflowOutbox).where(WorkflowOutbox.run_id == run_id))
            event_id = event.id
            assert (await session.get(AgentRun, run_id)).status == "pending"
        await process_event(event_id, raise_errors=True)
        async with SessionLocal() as session:
            event = await session.get(WorkflowOutbox, event_id)
            assert event.processed, event.last_error
            assert (await session.get(AgentRun, run_id)).status == "waiting_approval"
            count = len((await session.scalars(select(AgentStep).where(AgentStep.run_id == run_id))).all())
        await process_event(event_id)
        async with SessionLocal() as session:
            assert len((await session.scalars(select(AgentStep).where(AgentStep.run_id == run_id))).all()) == count == 11
    finally:
        settings.execution_mode = original


@pytest.mark.parametrize("change", ["user_deleted", "tenant_deleted", "platform_account", "user_disabled", "tenant_disabled", "membership_disabled"])
async def test_worker_rejects_revoked_identity_before_executing(client, monkeypatch, change):
    from datetime import UTC, datetime

    from fastapi import HTTPException

    from backend.models import Tenant, TenantMembership, User

    monkeypatch.setattr(get_settings(), "execution_mode", "worker")
    p = await project(client)
    started = await client.post(f"/api/projects/{p['id']}/runs", headers={"Idempotency-Key": uuid.uuid4().hex})
    run_id = started.json()["data"]["id"]
    async with SessionLocal() as session:
        event = await session.scalar(select(WorkflowOutbox).where(WorkflowOutbox.run_id == run_id))
        event_id = event.id
        user = await session.get(User, event.actor_id)
        tenant = await session.get(Tenant, event.tenant_id)
        membership = await session.scalar(select(TenantMembership).where(TenantMembership.user_id == user.id, TenantMembership.tenant_id == tenant.id))
        if change == "user_deleted":
            user.deleted_at = datetime.now(UTC)
        elif change == "tenant_deleted":
            tenant.deleted_at = datetime.now(UTC)
        elif change == "platform_account":
            user.account_type = "platform"
        elif change == "user_disabled":
            user.status = "disabled"
        elif change == "tenant_disabled":
            tenant.status = "disabled"
        else:
            membership.status = "disabled"
        await session.commit()
    assert (await client.get("/api/auth/me")).status_code == 401
    with pytest.raises(HTTPException) as exc:
        await process_event(event_id, raise_errors=True)
    assert exc.value.status_code == 403
    # Normal consumption records the rejected event without escalating privileges.
    await process_event(event_id)
    async with SessionLocal() as session:
        assert not list(await session.scalars(select(AgentStep).where(AgentStep.run_id == run_id)))
        assert (await session.get(WorkflowOutbox, event_id)).processed


async def test_worker_personal_scope_matches_owner_and_space_kind(client):
    from types import SimpleNamespace

    from fastapi import HTTPException

    from backend.models import Tenant, User
    from backend.worker import actor_context

    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.email == "company-admin@example.com"))
        personal = await session.scalar(select(Tenant).where(Tenant.kind == "personal", Tenant.personal_owner_id == user.id))
        event = SimpleNamespace(actor_id=user.id, tenant_id=personal.id)
        principal = await actor_context(session, event)
        assert principal.workspace_kind == "personal" and principal.tenant_id == personal.id
        personal.personal_owner_id = uuid.uuid4().hex
        await session.flush()
        with pytest.raises(HTTPException) as exc:
            await actor_context(session, event)
        assert exc.value.status_code == 403
