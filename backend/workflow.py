from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import delivery
from backend.config import get_settings
from backend.intelligence import PROMPT_VERSION, ExtractedRequirements, GapAssessment, structured
from backend.knowledge import retrieve
from backend.models import AgentRun, AgentStep, Approval, ImportJob, Project
from backend.schemas import (
    ConfigurationChange,
    GapAnalysisItem,
    ImplementationGraphState,
    ImplementationPlan,
    MilestoneSpec,
    RequirementSpec,
    RunStatus,
)
from backend.state_machine import (
    ProjectLifecycle,
    RunLifecycle,
    transition_project,
    transition_run,
)
from backend.workflow_definition import APPROVAL_NODES, NODES


def _extract_requirements(text: str) -> list[RequirementSpec]:
    rules = {
        "organization": ("部门", "组织", "成员"),
        "permission": ("权限", "角色", "审批"),
        "workflow": ("流程", "状态", "模板"),
        "migration": ("csv", "导入", "迁移"),
        "training": ("培训", "faq"),
    }
    found = []
    lowered = text.lower()
    for category, terms in rules.items():
        if any(term in lowered for term in terms):
            found.append(RequirementSpec(category=category, statement=text.strip()))
    # 项目创建已经完成必填校验。未命中预设关键词不代表资料缺失，
    # 应保留客户原始描述作为通用需求，继续进入分析与人工审批。
    return found or [RequirementSpec(category="general", statement=text.strip())]


async def _record(session: AsyncSession, run: AgentRun, node: str, detail: dict) -> None:
    from backend.observability import span_measurement
    measurement = span_measurement()
    if measurement:
        detail = {**detail, "execution": measurement}
    last_sequence = await session.scalar(
        select(func.max(AgentStep.sequence)).where(AgentStep.run_id == run.id)
    )
    session.add(AgentStep(
        tenant_id=run.tenant_id,
        run_id=run.id,
        sequence=(last_sequence or 0) + 1,
        node=node,
        status="completed",
        detail=detail,
    ))
    run.current_node = node
    run.updated_at = datetime.now(UTC)
    await session.flush()


async def advance(session: AsyncSession, run: AgentRun, *, one_node: bool = False) -> AgentRun:
    from backend.observability import span
    from backend.run_snapshots import execution_scope
    async with execution_scope(session, run):
        with span("workflow.advance", run_id=run.id, trace_id=run.trace_id):
            return await _advance(session, run, one_node=one_node)


async def _advance(session: AsyncSession, run: AgentRun, *, one_node: bool = False) -> AgentRun:
    from backend.agent_loop import advance_round
    while run.status == "running":
        await advance_round(session, run)
        if one_node:
            break
    return run


async def execute_workflow_node(session: AsyncSession, run: AgentRun, *, one_node: bool = False) -> AgentRun:
    if run.status != "running":
        raise HTTPException(409, "只有运行中的任务可以推进")
    state = ImplementationGraphState.model_validate(run.state)
    project = await session.get(Project, run.project_id)
    if not project:
        raise ValueError("Project not found")
    from backend.run_snapshots import requirements
    requirements_text = await requirements(session, run, project)

    start = NODES.index(state.current_node) if state.current_node in NODES else 0
    if state.current_node in state.completed_nodes:
        start += 1

    from backend.observability import span
    for node in NODES[start:]:
        with span("workflow.node", node=node, run_id=run.id):
            state.current_node = node
            from backend.skill_runtime import activate
            await activate(session, run, node)
            detail: dict = {}
            if node == "collect_requirements":
                state.requirements = _extract_requirements(requirements_text)
                if get_settings().model_mode != "deterministic":
                    extracted = await structured("提取实施需求，source 必须标识客户原文", {"requirements": requirements_text}, ExtractedRequirements)
                    state.requirements = extracted.requirements
                    state.requirements.append(RequirementSpec(category="original", statement=requirements_text))
                detail = {"count": len(state.requirements), "agent": "requirement_analyzer", "prompt_version": PROMPT_VERSION, "mode": get_settings().model_mode}
            elif node == "retrieve_product_knowledge":
                query = " ".join(r.statement for r in state.requirements)
                detail = {"citations": await retrieve(session, project.tenant_id, query, project_ids=[project.id])}
            elif node == "gap_analysis":
                items = []
                from backend.tool_registry import official_evidence
                official_sources = await official_evidence(session, run)
                for req in state.requirements:
                    evidence = await retrieve(session, project.tenant_id, req.statement, limit=2, project_ids=[project.id])
                    evidence.extend(official_sources)
                    items.append(GapAnalysisItem(requirement=req.statement, capability=evidence[0]["title"] if evidence else None,
                        fit="human_review", evidence_ids=[e["id"] for e in evidence],
                        recommendation="已召回候选资料，请独立核对能力适配" if evidence else "证据不足，转人工确认"))
                    if get_settings().model_mode != "deterministic" and evidence:
                        task = "评估这一条需求的能力差距；requirement 必须原样返回；只能引用提供的证据 ID，证据不能证明时返回 human_review"
                        if official_sources:
                            task += "。官方页面仅证明其所属产品的公开能力，不能证明内置模拟 SaaS 或本次部署的能力与执行；产品不匹配或页面版本未核实时返回 human_review"
                        assessed = await structured(task, {"requirement": req.statement, "evidence": evidence}, GapAssessment)
                        item = assessed.items[0]
                        if item.requirement != req.statement or not set(item.evidence_ids) <= {e["id"] for e in evidence} or (item.fit in {"supported", "partial"} and not item.evidence_ids):
                            raise HTTPException(422, "差距分析引用或需求不匹配，需要人工复核")
                        items[-1] = item
                state.gap_items = items
            elif node == "generate_implementation_plan":
                state.plan = ImplementationPlan(milestones=[
                    MilestoneSpec(name="调研与方案", days=3, owner_role="implementation_consultant"),
                    MilestoneSpec(name="配置与迁移", days=5, owner_role="implementation_consultant", dependencies=["调研与方案"]),
                    MilestoneSpec(name="培训与上线", days=2, owner_role="customer_contact", dependencies=["配置与迁移"]),
                ], assumptions=["客户审批人在两个工作日内反馈"], risks=["源数据质量可能影响迁移"])
                if get_settings().model_mode != "deterministic":
                    state.plan = await structured("根据客户原文和差距制定里程碑、依赖、负责人、风险和假设，不得声称已执行", {"requirements": requirements_text, "gaps": [g.model_dump() for g in state.gap_items]}, ImplementationPlan)
                await delivery.artifact(session, project, run, "plan", "实施计划", state.plan.model_dump_json(indent=2))
            elif node == "inspect_tenant_configuration":
                ws = await delivery.workspace(session, project)
                detail = {"workspace_id": ws.id, "version": ws.version, "configuration": ws.configuration}
            elif node == "generate_configuration_changes":
                ws = await delivery.workspace(session, project)
                state.configuration_changes = [
                    ConfigurationChange(path="workflow.statuses", old_value=ws.configuration["workflow.statuses"], new_value=["待办", "进行中", "审核中", "完成"], risk="high", reason="标准实施建议，由独立审批人核对客户需求"),
                    ConfigurationChange(path="notifications.due_date", old_value=ws.configuration["notifications.due_date"], new_value=True, risk="medium", reason="标准实施建议：启用到期提醒"),
                ]
                from backend.method_reports import write
                await write(session, project, run, "configuration_review", "配置方案评审", {
                    "requirements": requirements_text, "current_configuration": ws.configuration,
                    "proposed_changes": [c.model_dump(mode="json") for c in state.configuration_changes]}, [
                    {"title": "变更与影响", "guidance": "增加审核状态并启用到期提醒。由客户核实具体业务流程、部门和角色需要；本方案尚未执行。", "evidence_ids": ["proposed_changes", "requirements"]},
                    {"title": "审批与回读", "guidance": f"当前配置版本 {ws.version}。核对差异、通知影响和回退需求后交独立审批人；执行后回读目标配置。", "evidence_ids": ["current_configuration"]}])
            elif node == "apply_configuration":
                detail = await delivery.apply_configuration(session, project, run, state)
            elif node == "validate_import_files":
                job = await session.get(ImportJob, str(state.import_job_id)) if state.import_job_id else None
                if await delivery.migration_required(session, project, run) and not (job and job.validation.get("valid")):
                    state.blocking_reason = "请上传有效的成员 CSV，包含项目规定的部门、角色和管理员"
                    state.status = RunStatus.PREPARING_MATERIALS
                    transition_run(run, RunLifecycle.PREPARING_MATERIALS)
                    run.current_node = node
                    run.state = state.model_dump(mode="json")
                    await session.flush()
                    return run
                detail = {"status": "validated" if job else "skipped", "reason": "材料已校验" if job else "项目无需成员迁移"}
                from backend.method_reports import write
                await write(session, project, run, "migration_preparation", "成员迁移准备", {
                    "validation": job.validation if job else detail}, [
                    {"title": "导入准备", "guidance": "按映射核对姓名、邮箱、部门与角色，修正错误行，确认管理员。正式执行前仍需独立审批并重新校验目标状态。" if job else "本次执行无需成员迁移，未创建或执行导入任务。", "evidence_ids": ["validation"]}])
            elif node == "execute_import":
                job = await session.get(ImportJob, str(state.import_job_id)) if state.import_job_id else None
                detail = await delivery.execute_csv(session, project, run, state, job) if job else {"status": "skipped", "reason": "项目无需成员迁移"}
            elif node == "generate_training_materials":
                detail = await delivery.training(session, project, run)
            elif node == "run_go_live_checks":
                state.acceptance_report = await delivery.go_live_checks(session, project, run, state)
                from backend.method_reports import write
                await write(session, project, run, "acceptance_notes", "验收对照与整改建议", {
                    "actual_checks": state.acceptance_report.model_dump(mode="json")}, [
                    {"title": "实际检查与待办", "guidance": "业务检查已通过，等待独立验收审批。" if state.acceptance_report.ready else "仍需整改：" + "；".join(state.acceptance_report.blockers), "evidence_ids": ["actual_checks"]}])
                await delivery.sync_remediation(session, project, run, state.acceptance_report)
                await delivery.artifact(session, project, run, "acceptance", "上线验收报告", state.acceptance_report.model_dump_json(indent=2))
                if not state.acceptance_report.ready:
                    state.blocking_reason = "；".join(state.acceptance_report.blockers)
                    state.status = RunStatus.BLOCKED
                    transition_run(run, RunLifecycle.BLOCKED)
                    transition_project(project, ProjectLifecycle.BLOCKED)
                    await _record(session, run, node, state.acceptance_report.model_dump())
                    run.state = state.model_dump(mode="json")
                    return run
            elif node == "close_project":
                await delivery.require_receipt(session, project, run, state, "acceptance")
                await delivery.artifact(session, project, run, "summary", "实施总结", f"# {project.name}\n\n项目已通过独立验收。\n\nRun: {run.id}\n\n" + state.model_dump_json(indent=2))
                transition_project(project, ProjectLifecycle.COMPLETED)
                state.status = RunStatus.SUCCEEDED
                transition_run(run, RunLifecycle.SUCCEEDED)

            if node in APPROVAL_NODES:
                approval = Approval(
                    tenant_id=run.tenant_id,
                    run_id=run.id,
                    kind=APPROVAL_NODES[node].value,
                    payload=await delivery.approval_material(session, project, run, state, APPROVAL_NODES[node].value),
                    requested_by=run.started_by or "agent",
                )
                session.add(approval)
                await session.flush()
                state.pending_approval_id = UUID(approval.id)
                state.status = RunStatus.WAITING_APPROVAL
                transition_run(run, RunLifecycle.WAITING_APPROVAL)
                await _record(session, run, node, {"approval_id": approval.id})
                state.completed_nodes.append(node)
                run.state = state.model_dump(mode="json")
                await session.flush()
                return run

            await _record(session, run, node, detail)
            state.completed_nodes.append(node)
            run.state = state.model_dump(mode="json")
            if one_node:
                await session.flush()
                return run

    await session.flush()
    return run


async def resume_after_approval(session: AsyncSession, approval: Approval, decision: str) -> AgentRun:
    run = await session.get(AgentRun, approval.run_id)
    if not run:
        raise ValueError("Run not found")
    state = ImplementationGraphState.model_validate(run.state)
    state.pending_approval_id = None
    if decision == "rejected":
        state.status = RunStatus.FAILED
        transition_run(run, RunLifecycle.FAILED)
        project = await session.get(Project, run.project_id)
        if project:
            transition_project(project, ProjectLifecycle.BLOCKED)
        run.state = state.model_dump(mode="json")
        await session.flush()
        return run
    state.status = RunStatus.RUNNING
    transition_run(run, RunLifecycle.RUNNING)
    run.state = state.model_dump(mode="json")
    await session.flush()
    from backend.execution import dispatch
    return await dispatch(session, run)
