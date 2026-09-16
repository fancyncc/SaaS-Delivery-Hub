import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from test_api import project

from backend.agent_loop import default_plan, evaluate, validate_plan
from backend.agent_models import AgentAction
from backend.agent_types import ActionBatch, AgentState, StagePlan, ToolObservation
from backend.config import get_settings
from backend.db import SessionLocal
from backend.models import AgentRun, WorkflowOutbox


def test_planner_rejects_cycles_and_missing_business_prerequisite():
    plan = default_plan("configuration").model_dump()
    plan["milestones"][0]["dependencies"] = [plan["milestones"][1]["id"]]
    with pytest.raises(ValidationError):
        StagePlan.model_validate(plan)
    plan = default_plan("configuration")
    plan.milestones[1].dependencies = []
    with pytest.raises(ValueError):
        validate_plan(plan, "configuration")


def test_out_of_stage_tool_and_oversized_batch_rejected():
    plan = default_plan("configuration")
    plan.milestones[0].tool = "execute_import"
    with pytest.raises(ValueError):
        validate_plan(plan, "configuration")
    with pytest.raises(ValidationError):
        ActionBatch.model_validate({"actions": [{"tool": "knowledge_search"}] * 4})


def test_evaluator_branches():
    agent = AgentState()
    milestone = default_plan("configuration").milestones[0]
    obs = ToolObservation(action_id="test", tool=milestone.tool, status="failed")
    assert evaluate(agent, milestone, obs).outcome == "retry"
    agent.retries[milestone.id] = 2
    assert evaluate(agent, milestone, obs).outcome == "replan"
    agent.replans = 2
    assert evaluate(agent, milestone, obs).outcome == "blocked"
    obs.status = "succeeded"
    assert evaluate(agent, milestone, obs).outcome == "pass"


async def test_v2_preserves_approval_and_journals_actions(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_engine", "v2")
    p = await project(client)
    response = await client.post(f"/api/projects/{p['id']}/runs", headers={"Idempotency-Key": str(uuid.uuid4())})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    detail = (await client.get(f"/api/runs/{data['id']}")).json()["data"]
    assert detail["status"] == "waiting_approval", detail
    assert detail["state"]["engine_version"] == "v2"
    assert len(detail["state"]["agent"]["completed"]) == 5
    async with SessionLocal() as session:
        actions = (await session.scalars(select(AgentAction).where(AgentAction.run_id == data["id"]))).all()
        assert len(actions) == 5 and all(a.status == "succeeded" for a in actions)


async def test_budget_blocks_and_resume_preserves_completed_actions(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_engine", "v2")
    monkeypatch.setattr(get_settings(), "agent_max_rounds", 1)
    p = await project(client)
    started = (await client.post(f"/api/projects/{p['id']}/runs", headers={"Idempotency-Key": str(uuid.uuid4())})).json()["data"]
    assert started["status"] == "blocked"
    detail = (await client.get(f"/api/runs/{started['id']}")).json()["data"]
    assert "resume" in detail["allowed_actions"]
    monkeypatch.setattr(get_settings(), "agent_max_rounds", 20)
    response = await client.post(f"/api/runs/{started['id']}/resume", headers={"Idempotency-Key": str(uuid.uuid4())}, json={"expected_version": detail["version"], "reason": "已提高运行预算"})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "waiting_approval"
    async with SessionLocal() as session:
        actions = (await session.scalars(select(AgentAction).where(AgentAction.run_id == started["id"]))).all()
        assert len(actions) == 5


async def test_v2_worker_duplicate_delivery(client, monkeypatch):
    from backend.worker import process_event
    monkeypatch.setattr(get_settings(), "agent_engine", "v2")
    monkeypatch.setattr(get_settings(), "execution_mode", "worker")
    p = await project(client)
    started = (await client.post(f"/api/projects/{p['id']}/runs", headers={"Idempotency-Key": str(uuid.uuid4())})).json()["data"]
    async with SessionLocal() as session:
        event = await session.scalar(select(WorkflowOutbox).where(WorkflowOutbox.run_id == started["id"]))
        event_id = event.id
    await process_event(event_id, raise_errors=True)
    await process_event(event_id, raise_errors=True)
    async with SessionLocal() as session:
        run = await session.get(AgentRun, started["id"])
        assert run.status == "waiting_approval", run.state
        assert len((await session.scalars(select(AgentAction).where(AgentAction.run_id == run.id))).all()) == 5


async def test_transient_failure_recovers_and_verifies_memory(client, monkeypatch):
    from backend import workflow
    from backend.agent_models import Experience
    from backend.memory import recall
    monkeypatch.setattr(get_settings(), "agent_engine", "v2")
    original = workflow.advance_legacy
    calls = 0
    async def fail_once(session, run, **kwargs):
        nonlocal calls
        if run.state["current_node"] == "collect_requirements":
            calls += 1
            if calls == 1:
                raise TimeoutError("sensitive customer secret")
        return await original(session, run, **kwargs)
    monkeypatch.setattr(workflow, "advance_legacy", fail_once)
    p = await project(client)
    response = await client.post(f"/api/projects/{p['id']}/runs", headers={"Idempotency-Key": str(uuid.uuid4())})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "waiting_approval", response.text
    async with SessionLocal() as session:
        entries = list(await session.scalars(select(Experience)))
        assert len(entries) == 1 and entries[0].verified
        assert "secret" not in entries[0].advice
        run = await session.get(AgentRun, response.json()["data"]["id"])
        assert len(await recall(session, run, "collect_requirements")) == 1
        run.project_id = str(uuid.uuid4())
        with session.no_autoflush:
            assert await recall(session, run, "collect_requirements") == []


async def test_permanent_failure_replans_then_blocks(client, monkeypatch):
    from backend import workflow
    monkeypatch.setattr(get_settings(), "agent_engine", "v2")
    original = workflow.advance_legacy
    async def failing(session, run, **kwargs):
        if run.state["current_node"] == "collect_requirements":
            raise TimeoutError("unavailable")
        return await original(session, run, **kwargs)
    monkeypatch.setattr(workflow, "advance_legacy", failing)
    p = await project(client)
    response = await client.post(f"/api/projects/{p['id']}/runs", headers={"Idempotency-Key": str(uuid.uuid4())})
    assert response.status_code == 200, response.text
    run_id = response.json()["data"]["id"]
    detail = (await client.get(f"/api/runs/{run_id}")).json()["data"]
    assert detail["status"] == "blocked", detail
    assert detail["state"]["agent"]["replans"] == 2
    assert detail["state"]["agent"]["evaluation"]["outcome"] == "blocked"


async def test_v2_full_approval_chain_and_failed_acceptance(client, monkeypatch):
    from test_delivery import preparing
    monkeypatch.setattr(get_settings(), "agent_engine", "v2")
    p, run_id, approver = await preparing(client)
    try:
        response = await client.post("/api/imports/validate", headers={"Idempotency-Key": str(uuid.uuid4())}, json={"project_id": p["id"], "run_id": run_id, "csv_text": "name,email,department,role\nA,a@example.com,设计部,admin"})
        assert response.status_code == 200, response.text
        approval = next(a for a in (await approver.get(f"/api/approvals?run_id={run_id}")).json()["data"] if a["status"] == "pending")
        response = await approver.post(f"/api/approvals/{approval['id']}/decision", headers={"Idempotency-Key": str(uuid.uuid4())}, json={"decision": "approved", "expected_version": approval["version"]})
        assert response.status_code == 200, response.text
        assert response.json()["data"]["run_status"] == "blocked", response.text
        detail = (await client.get(f"/api/runs/{run_id}")).json()["data"]
        assert detail["state"]["acceptance_report"]["ready"] is False
        assert "execute_import" in detail["state"]["completed_nodes"]
    finally:
        await approver.aclose()


async def test_generator_unknown_arguments_cannot_execute(client, monkeypatch):
    from backend import agent_loop
    monkeypatch.setattr(get_settings(), "agent_engine", "v2")
    monkeypatch.setattr(get_settings(), "model_mode", "real")
    async def malicious(task, source, schema):
        if schema is StagePlan:
            return default_plan("discovery")
        return ActionBatch.model_validate({"actions": [{"tool": "execute_import", "arguments": {}}]})
    monkeypatch.setattr(agent_loop, "structured", malicious)
    p = await project(client)
    result = (await client.post(f"/api/projects/{p['id']}/runs", headers={"Idempotency-Key": str(uuid.uuid4())})).json()["data"]
    assert result["status"] == "blocked"
    async with SessionLocal() as session:
        assert not list(await session.scalars(select(AgentAction).where(AgentAction.run_id == result["id"])))


async def test_nested_model_budget_is_checked_before_request(monkeypatch):
    import httpx

    from backend.agent_budget import BudgetExceeded, ModelScope, model_scope
    from backend.intelligence import structured
    settings = get_settings()
    monkeypatch.setattr(settings, "model_api_key", "test")
    monkeypatch.setattr(settings, "model_base_url", "https://example.test/v1")
    monkeypatch.setattr(settings, "model_name", "test")
    monkeypatch.setattr(settings, "agent_token_budget", 1000)
    token = model_scope.set(ModelScope(AgentState(), {}))
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(500)
    try:
        with pytest.raises(BudgetExceeded):
            await structured("test", {}, ActionBatch, transport=httpx.MockTransport(handler))
        assert not calls
    finally:
        model_scope.reset(token)


async def test_resume_rechecks_original_actor_and_version(client, monkeypatch):
    from backend.models import TenantMembership
    monkeypatch.setattr(get_settings(), "agent_engine", "v2")
    monkeypatch.setattr(get_settings(), "agent_max_rounds", 1)
    p = await project(client)
    result = (await client.post(f"/api/projects/{p['id']}/runs", headers={"Idempotency-Key": str(uuid.uuid4())})).json()["data"]
    response = await client.post(f"/api/runs/{result['id']}/resume", headers={"Idempotency-Key": str(uuid.uuid4())}, json={"expected_version": result["version"] - 1, "reason": "恢复测试"})
    assert response.status_code == 409
    async with SessionLocal() as session:
        run = await session.get(AgentRun, result["id"])
        membership = await session.scalar(select(TenantMembership).where(TenantMembership.user_id == run.started_by))
        membership.status = "disabled"
        await session.commit()
    response = await client.post(f"/api/runs/{result['id']}/resume", headers={"Idempotency-Key": str(uuid.uuid4())}, json={"expected_version": result["version"], "reason": "恢复测试"})
    assert response.status_code in {401, 403, 404}


async def test_v2_personal_project_completes_all_approvals(client, monkeypatch):
    from test_open_registration import test_personal_complete_workflow_and_company_isolation
    monkeypatch.setattr(get_settings(), "agent_engine", "v2")
    await test_personal_complete_workflow_and_company_isolation(client)


async def test_hybrid_planner_can_insert_three_search_actions(client, monkeypatch):
    from backend import agent_loop, workflow
    from backend.agent_types import Criterion, Milestone
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_engine", "v2")
    monkeypatch.setattr(settings, "model_mode", "real")
    async def model(task, source, schema):
        if schema is StagePlan:
            plan = default_plan("discovery")
            plan.milestones[0].dependencies = ["research"]
            plan.milestones.insert(0, Milestone(id="research", title="核对产品资料", tool="knowledge_search", criteria=[Criterion(tool="knowledge_search")]))
            return plan
        tool = source["milestone"]["tool"]
        actions = [{"tool": tool, "arguments": {"query": q}} for q in ("成员权限", "导入模板", "上线验收")] if tool == "knowledge_search" else [{"tool": tool}]
        return ActionBatch.model_validate({"actions": actions})
    monkeypatch.setattr(agent_loop, "structured", model)
    original = workflow.advance_legacy
    async def deterministic_business(session, run, **kwargs):
        settings.model_mode = "deterministic"
        try:
            return await original(session, run, **kwargs)
        finally:
            settings.model_mode = "real"
    monkeypatch.setattr(workflow, "advance_legacy", deterministic_business)
    p = await project(client)
    result = (await client.post(f"/api/projects/{p['id']}/runs", headers={"Idempotency-Key": str(uuid.uuid4())})).json()["data"]
    assert result["status"] == "waiting_approval", result
    async with SessionLocal() as session:
        actions = list(await session.scalars(select(AgentAction).where(AgentAction.run_id == result["id"])))
        searches = [a for a in actions if a.tool == "knowledge_search"]
        assert len(actions) == 8 and len({a.id for a in searches}) == 3
