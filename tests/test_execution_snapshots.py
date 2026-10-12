import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DatabaseError
from test_api import project

from backend.config import get_settings
from backend.db import SessionLocal
from backend.models import AgentRun, Approval, Project, ProjectDocument, WorkflowOutbox
from backend.run_snapshots import (
    RunSnapshot,
    document,
    execution_scope,
    inputs,
    matches,
    requirements,
)
from backend.schemas import ImplementationGraphState
from backend.worker import process_event


async def start(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "execution_mode", "worker")
    p = await project(client)
    response = await client.post(f"/api/projects/{p['id']}/runs", headers={"Idempotency-Key": uuid.uuid4().hex})
    assert response.status_code == 200, response.text
    return p, response.json()["data"]["id"]


async def test_queued_run_uses_original_requirements_and_document(client, monkeypatch):
    p, run_id = await start(client, monkeypatch)
    async with SessionLocal() as session:
        run = await session.get(AgentRun, run_id)
        proj = await session.get(Project, p["id"])
        expected = proj.requirements_text
        doc = await session.scalar(select(ProjectDocument).where(ProjectDocument.project_id == proj.id))
        proj.requirements_text = "已修改原始需求，不再执行任何导入工作。"
        doc.content = {**doc.content, "employee_count": 999, "migration_scope": "不迁移"}
        await session.commit()
        assert await requirements(session, run, proj) == expected
        assert (await document(session, run, proj))["employee_count"] == 80
        event = await session.scalar(select(WorkflowOutbox).where(WorkflowOutbox.run_id == run_id))
        event_id = event.id
    await process_event(event_id, raise_errors=True)
    async with SessionLocal() as session:
        run = await session.get(AgentRun, run_id)
        state = ImplementationGraphState.model_validate(run.state)
        assert any(r.statement == expected for r in state.requirements)
        approval = await session.scalar(select(Approval).where(Approval.run_id == run_id))
        from backend import delivery
        current = await delivery.material(session, await session.get(Project, p["id"]), run, state, "plan")
        assert await matches(session, run, approval.payload, current)
        assert approval.payload["revision_id"]


async def test_snapshots_cannot_be_updated_deleted_or_downgraded(client, monkeypatch):
    _, run_id = await start(client, monkeypatch)
    for statement in (
        update(RunSnapshot).where(RunSnapshot.run_id == run_id).values(payload={}),
        delete(RunSnapshot).where(RunSnapshot.run_id == run_id),
        update(AgentRun).where(AgentRun.id == run_id).values(snapshot_version=0),
    ):
        async with SessionLocal() as session:
            with pytest.raises(DatabaseError):
                await session.execute(statement)
            await session.rollback()


async def test_changed_model_identity_blocks_resume_but_key_rotation_does_not(client, monkeypatch):
    _, run_id = await start(client, monkeypatch)
    async with SessionLocal() as session:
        run = await session.get(AgentRun, run_id)
        monkeypatch.setattr(get_settings(), "model_api_key", "rotated-secret")
        async with execution_scope(session, run):
            pass
        monkeypatch.setattr(get_settings(), "model_name", "different-model")
        with pytest.raises(HTTPException) as exc:
            async with execution_scope(session, run):
                pass
        assert exc.value.status_code == 409


async def test_snapshot_api_scope_and_missing_snapshot_fail_closed(client, monkeypatch):
    _, run_id = await start(client, monkeypatch)
    summaries = (await client.get(f"/api/runs/{run_id}/snapshots")).json()["data"]
    assert summaries["status"] == "protected"
    item_id = summaries["revisions"][0]["id"]
    response = await client.get(f"/api/runs/{run_id}/snapshots/{item_id}")
    assert response.status_code == 200
    assert "api_key" not in response.text and "rotated-secret" not in response.text
    assert (await client.get(f"/api/runs/{run_id}/snapshots/{uuid.uuid4()}")).status_code == 404
    async with SessionLocal() as session:
        # Emulate storage corruption after a privileged maintenance mistake.
        await session.execute(text("DROP TRIGGER run_snapshots_no_delete"))
        await session.execute(delete(RunSnapshot).where(RunSnapshot.run_id == run_id))
        await session.commit()
        with pytest.raises(HTTPException):
            await inputs(session, await session.get(AgentRun, run_id))


async def test_legacy_run_remains_explicitly_unprotected(client):
    p = await project(client)
    async with SessionLocal() as session:
        proj = await session.get(Project, p["id"])
        session.add(AgentRun(tenant_id=proj.tenant_id, project_id=p["id"], run_number=1, trace_id="a" * 32))
        await session.commit()
        run = await session.scalar(select(AgentRun).where(AgentRun.project_id == p["id"]))
        response = await client.get(f"/api/runs/{run.id}/snapshots")
        assert response.json()["data"] == {"version": 0, "status": "unprotected", "revisions": []}


async def test_actual_model_payload_and_provider_response_are_journaled_without_key(client, monkeypatch):
    import httpx
    from pydantic import BaseModel

    from backend.intelligence import structured
    settings = get_settings()
    for name, value in {"model_mode": "real", "model_name": "probe", "model_base_url": "https://example.test/v1",
                        "model_api_style": "chat_completions", "model_api_key": "never-journal-this-key"}.items():
        monkeypatch.setattr(settings, name, value)
    _, run_id = await start(client, monkeypatch)
    class Reply(BaseModel):
        status: str
    def provider(request):
        return httpx.Response(200, json={"id": "provider-response", "model": "probe-revision-1",
            "choices": [{"finish_reason": "stop", "message": {"content": '{"status":"ok"}'}}]})
    async with SessionLocal() as session:
        run = await session.get(AgentRun, run_id)
        async with execution_scope(session, run):
            result = await structured("验收探测", {"material": "原始材料"}, Reply, transport=httpx.MockTransport(provider))
        assert result.status == "ok"
        revisions = list(await session.scalars(select(RunSnapshot).where(RunSnapshot.run_id == run_id)))
        kinds = {r.kind: r for r in revisions}
        assert "原始材料" in str(kinds["model_request"].payload)
        assert kinds["model_response"].payload["provider_model"] == "probe-revision-1"
        assert "never-journal-this-key" not in str([r.payload for r in revisions])


async def test_agent_action_replay_uses_frozen_requirements(client, monkeypatch):
    from backend.agent_loop import default_plan, execute_action
    from backend.agent_models import AgentAction
    from backend.agent_types import ToolAction

    p, run_id = await start(client, monkeypatch)
    async with SessionLocal() as session:
        run = await session.get(AgentRun, run_id)
        state = ImplementationGraphState.model_validate(run.state)
        state.agent.stage = "discovery"
        from backend.state_machine import RunLifecycle, transition_run
        transition_run(run, RunLifecycle.RUNNING)
        state.status = "running"
        milestone = default_plan("discovery").milestones[0]
        action = ToolAction(tool=milestone.tool)
        async with execution_scope(session, run):
            first = await execute_action(session, run, state, milestone, action)
        assert first.status == "succeeded", first
        await session.commit()
        proj = await session.get(Project, p["id"])
        proj.requirements_text = "项目需求后来被编辑，既有执行仍必须使用原始输入。"
        await session.commit()
        async with execution_scope(session, run):
            replay = await execute_action(session, run, state, milestone, action)
        assert replay == first and replay.status == "succeeded"
        assert len(list(await session.scalars(select(AgentAction).where(AgentAction.run_id == run_id)))) == 1


async def test_agent_context_ranking_uses_frozen_requirements(client, monkeypatch):
    from backend import agent_loop, context_budget
    from backend.knowledge import lexemes

    p, run_id = await start(client, monkeypatch)
    async with SessionLocal() as session:
        run = await session.get(AgentRun, run_id)
        proj = await session.get(Project, p["id"])
        frozen = proj.requirements_text
        proj.requirements_text = "量子宇宙飞船轨道"
        await session.commit()
        evidence = {"text": frozen}
        async def retrieve(*args, **kwargs):
            return [evidence]
        async def recall(*args, **kwargs):
            return []
        selected = []
        original = context_budget.ContextBundle.select
        def select_context(bundle, budget):
            selected.extend(bundle.blocks)
            return original(bundle, budget)
        monkeypatch.setattr(agent_loop, "retrieve", retrieve)
        monkeypatch.setattr(agent_loop.memory, "recall", recall)
        monkeypatch.setattr(context_budget.ContextBundle, "select", select_context)
        context = await agent_loop.context_for(session, run, ImplementationGraphState.model_validate(run.state), "create_project")
        assert context["requirements"] == frozen
        block = next(b for b in selected if b.category == "knowledge")
        terms = set(lexemes(frozen).split())
        expected = len(terms & set(lexemes(str(evidence)).split())) / max(1, len(terms))
        assert block.relevance == expected
