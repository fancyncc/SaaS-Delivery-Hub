"""Stage-scoped agent loop. Models propose; registry and business code authorize."""
import asyncio
import json
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

import httpx
from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import select

from backend import memory
from backend.agent_budget import BudgetExceeded, ModelScope, model_scope
from backend.agent_models import AgentAction
from backend.agent_types import (
    ActionBatch,
    AgentState,
    Criterion,
    EvaluationResult,
    Milestone,
    StagePlan,
    StrictModel,
    ToolAction,
    ToolObservation,
)
from backend.config import get_settings
from backend.delivery import digest
from backend.intelligence import structured
from backend.knowledge import retrieve
from backend.models import AgentStep, Project
from backend.schemas import ImplementationGraphState
from backend.state_machine import ProjectLifecycle, RunLifecycle, transition_project, transition_run

STAGES = {
    "discovery": ["create_project", "collect_requirements", "retrieve_product_knowledge", "gap_analysis", "generate_implementation_plan"],
    "configuration": ["inspect_tenant_configuration", "generate_configuration_changes"],
    "migration": ["apply_configuration", "validate_import_files"],
    "acceptance": ["execute_import", "generate_training_materials", "run_go_live_checks"],
    "closure": ["close_project"],
}


class NoArguments(StrictModel):
    pass


class SearchArguments(StrictModel):
    query: str = Field(min_length=1, max_length=2000)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    permission: str
    risk: str
    parameters: type[StrictModel] = NoArguments
    timeout: int = 120
    idempotent: bool = True


REGISTRY = {tool: ToolSpec(tool, "import.execute" if tool == "execute_import" else "run.start",
    "high" if tool in {"apply_configuration", "execute_import", "close_project"} else "low")
    for tools in STAGES.values() for tool in tools}
REGISTRY["knowledge_search"] = ToolSpec("knowledge_search", "run.start", "low", SearchArguments)


def default_plan(stage: str) -> StagePlan:
    names = STAGES[stage]
    return StagePlan(milestones=[Milestone(id=name, title=name, tool=name,
        dependencies=names[i - 1:i], criteria=[Criterion(tool=name)]) for i, name in enumerate(names)])


def validate_plan(plan: StagePlan, stage: str, previous: StagePlan | None = None, completed: list[str] | None = None):
    nodes = {m.id: m for m in plan.milestones}
    required = STAGES[stage]
    by_tool = {tool: [m for m in plan.milestones if m.tool == tool] for tool in required}
    for m in plan.milestones:
        if m.tool not in {*required, "knowledge_search"} or any(c.tool != m.tool for c in m.criteria):
            raise ValueError("plan contains out-of-stage tool or invalid required criterion")
    if any(len(ms) != 1 for ms in by_tool.values()):
        raise ValueError("every required business tool must occur exactly once")

    def ancestors(m):
        values = set(m.dependencies)
        for dep in m.dependencies:
            values.update(ancestors(nodes[dep]))
        return values
    # Fixed business prerequisites are not model-editable; optional knowledge
    # gathering can be inserted anywhere within the stage DAG.
    for before, after in zip(required, required[1:], strict=False):
        if by_tool[before][0].id not in ancestors(by_tool[after][0]):
            raise ValueError("business prerequisite was removed")
    terminal = by_tool[required[-1]][0]
    if set(nodes) != ancestors(terminal) | {terminal.id}:
        raise ValueError("every optional milestone must lead to the stage completion")
    if previous:
        for m in previous.milestones:
            if m.id in (completed or []) and nodes.get(m.id) != m:
                raise ValueError("completed milestone cannot be changed")


async def authorize(session, run, spec):
    from backend.access import require_project_permission
    from backend.worker import actor_context
    principal = await actor_context(session, SimpleNamespace(actor_id=run.started_by, tenant_id=run.tenant_id))
    project = await session.get(Project, run.project_id)
    if not project or project.deleted_at:
        raise HTTPException(403, "项目已删除或不可访问")
    await require_project_permission(session, project, principal, "run.start")
    await require_project_permission(session, project, principal, spec.permission)
    return project


async def context_for(session, run, state, tool):
    project = await authorize(session, run, REGISTRY[tool])
    agent = state.agent
    agent.knowledge = await retrieve(session, run.tenant_id, project.requirements_text, project_ids=[project.id])
    agent.memories = await memory.recall(session, run, tool)
    context = {"requirements": project.requirements_text, "stage": agent.stage,
        "completed_nodes": state.completed_nodes, "constraints": ["不得越过阶段或审批", "经验不是产品证据"],
        "knowledge": agent.knowledge, "experiences": [m.model_dump() for m in agent.memories],
        "observations": [o.model_dump() for o in agent.observations[-5:]],
        "evaluation": agent.evaluation.model_dump() if agent.evaluation else None}
    # Bound by UTF-8 bytes, conservative for all supported model tokenizers.
    budget = get_settings().agent_context_tokens
    for key in ("observations", "experiences", "knowledge"):
        while len(json.dumps(context, ensure_ascii=False).encode()) > budget and context[key]:
            context[key].pop(0 if key == "observations" else -1)
    if len(json.dumps(context, ensure_ascii=False).encode()) > budget:
        raise BudgetExceeded("原始需求与硬约束超过上下文预算，需人工调整预算")
    return context


def evaluate(agent, milestone, observation, error_code="") -> EvaluationResult:
    if observation.status == "succeeded":
        outcome, reason = "pass", "工具业务校验通过，执行结果已记录"
    elif error_code in {"forbidden", "unknown_write", "budget"}:
        outcome, reason = "blocked", "需要人工处理权限、执行状态或预算"
    elif agent.retries.get(milestone.id, 0) < get_settings().agent_max_retries:
        outcome, reason = "retry", "工具执行未通过，带失败反馈重试"
    elif agent.replans < get_settings().agent_max_replans:
        outcome, reason = "replan", "重试额度耗尽，重新规划本阶段未完成里程碑"
    else:
        outcome, reason = "blocked", "重试与重规划额度耗尽"
    return EvaluationResult(outcome=outcome, reason=reason, milestone_id=milestone.id)


async def block(session, run, state, reason):
    state.status, state.blocking_reason = "blocked", reason
    run.current_node = state.current_node
    transition_run(run, RunLifecycle.BLOCKED)
    project = await session.get(Project, run.project_id)
    if project.lifecycle_status == "in_progress":
        transition_project(project, ProjectLifecycle.BLOCKED)
    run.state = state.model_dump(mode="json")
    run.version += 1
    await session.flush()


async def record_evaluation(session, run, state):
    from backend.workflow import _record
    current = run.current_node
    await _record(session, run, "agent_evaluation", {"stage": state.agent.stage,
        "plan_version": state.agent.plan_version, "round": state.agent.rounds,
        "evaluation": state.agent.evaluation.model_dump() if state.agent.evaluation else None,
        "tokens_reserved": state.agent.tokens_reserved})
    run.current_node = current


async def execute_action(session, run, state, milestone, action, ordinal=0):
    from backend.workflow import advance_legacy
    agent = state.agent
    spec = REGISTRY.get(action.tool)
    if spec is None or action.tool != milestone.tool:
        raise ValueError("unknown tool or action outside current milestone")
    arguments = spec.parameters.model_validate(action.arguments.model_dump(exclude_none=True)).model_dump()
    project = await authorize(session, run, spec)
    identifier = str(uuid5(NAMESPACE_URL, f"{run.id}:{agent.stage}:{agent.plan_version}:{milestone.id}:{agent.resume_count}:{agent.rounds}:{ordinal}"))
    checksum = digest({"tool": action.tool, "arguments": arguments, "requirements": project.requirements_text,
        "configuration": [c.model_dump() for c in state.configuration_changes], "import_job_id": str(state.import_job_id)})
    row = await session.get(AgentAction, identifier)
    if row:
        if row.request_digest != checksum or row.status != "succeeded":
            raise HTTPException(409, "存在执行状态不明的动作，需人工核对")
        return ToolObservation(action_id=row.id, tool=row.tool, status="succeeded", result=row.result)
    row = AgentAction(id=identifier, tenant_id=run.tenant_id, run_id=run.id, stage=agent.stage,
        milestone_id=milestone.id, tool=action.tool, request_digest=checksum, status="started")
    session.add(row)
    await session.flush()
    try:
        async with session.begin_nested():
            async with asyncio.timeout(spec.timeout):
                if action.tool == "knowledge_search":
                    result = {"citations": await retrieve(session, run.tenant_id, arguments["query"], project_ids=[project.id])}
                else:
                    if action.tool in state.completed_nodes:
                        raise ValueError("cannot replay completed business tool")
                    state.current_node = action.tool
                    run.state = state.model_dump(mode="json")
                    await advance_legacy(session, run, one_node=True)
                    updated = ImplementationGraphState.model_validate(run.state)
                    step = await session.scalar(select(AgentStep).where(AgentStep.run_id == run.id,
                        AgentStep.node == action.tool).order_by(AgentStep.sequence.desc()).limit(1))
                    result = step.detail if step else {"reason": updated.blocking_reason}
                row.status = "succeeded" if action.tool == "knowledge_search" or action.tool in run.state.get("completed_nodes", []) else "paused"
                row.result = result
                await session.flush()
    except (HTTPException, httpx.HTTPError, TimeoutError, ValueError, KeyError) as exc:
        # Rollback only the failed tool; keep its journal and previous successes.
        await session.refresh(run)
        row.status = "failed"
        if isinstance(exc, BudgetExceeded):
            code = "budget"
        elif isinstance(exc, (TimeoutError, httpx.TimeoutException)):
            code = "timeout"
        elif isinstance(exc, HTTPException) and exc.status_code in {401, 403, 404}:
            code = "forbidden"
        elif isinstance(exc, HTTPException) and exc.status_code == 409:
            code = "unknown_write"
        elif isinstance(exc, httpx.HTTPError) or isinstance(exc, HTTPException) and exc.status_code >= 500:
            code = "unavailable"
        else:
            code = "invalid_output"
        row.result = {"error_code": code}
    await session.flush()
    observation = ToolObservation(action_id=row.id, tool=row.tool, status=row.status, result=row.result)
    if row.status == "failed":
        await memory.reflect(session, run, row)
    if row.status == "succeeded":
        await memory.verify_recovery(session, run, row)
    return observation


async def advance_round(session, run):
    from backend.workflow import APPROVAL_NODES, NODES, advance_legacy
    state = ImplementationGraphState.model_validate(run.state)
    agent = state.agent
    next_tool = next((name for name in NODES if name not in state.completed_nodes), None)
    if not next_tool:
        await block(session, run, state, "任务状态与完成节点不一致，请人工核对")
        return run
    # Approval requests remain fixed workflow boundaries, inaccessible as tools.
    if next_tool in APPROVAL_NODES:
        await authorize(session, run, ToolSpec(next_tool, "run.start", "high"))
        state.current_node = next_tool
        run.state = state.model_dump(mode="json")
        return await advance_legacy(session, run, one_node=True)
    stage = next(name for name, nodes in STAGES.items() if next_tool in nodes)
    state.current_node = next_tool
    if agent.stage != stage:
        agent = AgentState(stage=stage, tokens_reserved=agent.tokens_reserved,
            observations=agent.observations[-5:], resume_count=agent.resume_count)
        state.agent = agent
    if agent.rounds >= get_settings().agent_max_rounds:
        agent.evaluation = EvaluationResult(outcome="blocked", reason="当前阶段已达到执行轮次上限", milestone_id=next_tool)
        await block(session, run, state, "当前阶段已达到执行轮次上限")
        await record_evaluation(session, run, state)
        return run
    scope_token = None
    try:
        context = await context_for(session, run, state, next_tool)
        scope_token = model_scope.set(ModelScope(agent, context))
        if agent.plan is None or agent.evaluation and agent.evaluation.outcome == "replan":
            previous = agent.plan
            candidate = default_plan(stage)
            if get_settings().model_mode != "deterministic":
                candidate = await structured("为当前阶段规划里程碑 DAG。每个必需工具恰好一次且维持依赖顺序；允许增加 knowledge_search；已完成里程碑原样保留", {
                    "required_plan": default_plan(stage).model_dump(), "previous": previous.model_dump() if previous else None,
                    "completed": agent.completed, "optional_tool": "knowledge_search"}, StagePlan)
            validate_plan(candidate, stage, previous, agent.completed)
            agent.plan, agent.plan_version = candidate, agent.plan_version + 1
            # Recover legacy-completed mandatory steps if resuming at a material gate.
            agent.completed = list(dict.fromkeys(agent.completed + [m.id for m in candidate.milestones if m.tool in state.completed_nodes]))
        ready = [m for m in agent.plan.milestones if m.id not in agent.completed and set(m.dependencies) <= set(agent.completed)]
        if not ready:
            raise ValueError("no runnable milestone")
        milestone = ready[0]
        context["milestone"] = milestone.model_dump()
        # One bounded milestone per durable round, up to 3 read-only searches.
        batch = ActionBatch(actions=[ToolAction(tool=milestone.tool,
            arguments={"query": context["requirements"][:2000]} if milestone.tool == "knowledge_search" else {})])
        if get_settings().model_mode != "deterministic":
            batch = await structured("只为当前里程碑生成工具动作。业务工具参数为空且只调用一次；knowledge_search 参数为 query。不得调用审批或其他里程碑工具", {
                "milestone": milestone.model_dump(), "parameters": REGISTRY[milestone.tool].parameters.model_json_schema()}, ActionBatch)
        if any(a.tool != milestone.tool for a in batch.actions) or milestone.tool != "knowledge_search" and len(batch.actions) != 1:
            raise ValueError("batch crosses milestone or repeats a business write")
        for action in batch.actions:
            REGISTRY[action.tool].parameters.model_validate(action.arguments.model_dump(exclude_none=True))
        agent.rounds += 1
        for ordinal, action in enumerate(batch.actions):
            observation = await execute_action(session, run, state, milestone, action, ordinal)
            # Legacy tool updates business fields; keep live budget/state object.
            state = ImplementationGraphState.model_validate(run.state)
            state.agent = agent
            state.current_node = milestone.tool
            run.current_node = milestone.tool
            agent.observations = (agent.observations + [observation])[-5:]
            if observation.status == "paused":
                agent.evaluation = EvaluationResult(outcome="blocked", reason=state.blocking_reason or "等待业务材料", milestone_id=milestone.id)
                break
            agent.evaluation = evaluate(agent, milestone, observation, observation.result.get("error_code", ""))
            if agent.evaluation.outcome == "pass":
                if ordinal == len(batch.actions) - 1 and milestone.id not in agent.completed:
                    agent.completed.append(milestone.id)
            elif agent.evaluation.outcome == "retry":
                agent.retries[milestone.id] = agent.retries.get(milestone.id, 0) + 1
                break
            elif agent.evaluation.outcome == "replan":
                agent.replans += 1
                break
            else:
                await block(session, run, state, agent.evaluation.reason)
                break
        run.state = state.model_dump(mode="json")
        run.version += 1
        await record_evaluation(session, run, state)
        await session.flush()
    except (BudgetExceeded, ValueError, HTTPException, httpx.HTTPError) as exc:
        reason = str(exc) if isinstance(exc, BudgetExceeded) else "规划、上下文或权限校验未通过，请检查配置与授权后恢复"
        agent.evaluation = EvaluationResult(outcome="blocked", reason=reason, milestone_id="planning")
        await block(session, run, state, reason)
        await record_evaluation(session, run, state)
    finally:
        if scope_token is not None:
            model_scope.reset(scope_token)
    return run
