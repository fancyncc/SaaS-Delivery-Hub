"""Code-owned lesson templates with independent durable-evidence validators."""
from sqlalchemy import select

from backend.agent_models import AgentAction, AgentLesson
from backend.agent_types import MemoryEntry
from backend.config import get_settings
from backend.models import Approval, ImportJob

WORKFLOW_VERSION = "agent-v2-context-1"
TEMPLATES = {
    "workflow": "同一阶段按固定里程碑依赖顺序执行，并逐项核对工具完成凭证。",
    "constraint_pattern": "高风险操作必须取得本次对应审批；历史批准不能替代当前审批。",
    "validation_rule": "成员导入前执行文件校验，核对必填字段和逐行错误后再推进。",
}


async def workflow(session, run, action):
    from backend.agent_loop import STAGES
    rows = list(await session.scalars(select(AgentAction).where(AgentAction.run_id == run.id,
        AgentAction.tenant_id == run.tenant_id, AgentAction.stage == action.stage, AgentAction.status == "succeeded")
        .order_by(AgentAction.created_at, AgentAction.id)))
    required = STAGES.get(action.stage, [])
    tools = [r.tool for r in rows if r.tool in required]
    if required and tools == required:
        return {"action_ids": [r.id for r in rows]}
    return None


async def constraint_pattern(session, run, action):
    kind = {"apply_configuration": "configuration", "execute_import": "import", "close_project": "acceptance"}.get(action.tool)
    if not kind or action.status != "succeeded":
        return None
    approval = await session.scalar(select(Approval).where(Approval.run_id == run.id, Approval.kind == kind,
        Approval.status == "approved", Approval.tenant_id == run.tenant_id))
    return {"action_ids": [action.id], "approval_id": approval.id} if approval else None


async def validation_rule(session, run, action):
    if action.tool != "validate_import_files" or action.status != "succeeded":
        return None
    jobs = list(await session.scalars(select(ImportJob).where(ImportJob.run_id == run.id, ImportJob.tenant_id == run.tenant_id)))
    valid = [j for j in jobs if j.validation.get("row_count", 0) > 0 and j.validation.get("errors") == []]
    return {"action_ids": [action.id], "import_job_ids": [j.id for j in valid]} if valid else None


VALIDATORS = {"workflow": workflow, "constraint_pattern": constraint_pattern, "validation_rule": validation_rule}


async def record(session, run, action):
    if not get_settings().agent_experience_enabled or action.status != "succeeded":
        return
    for category, advice in TEMPLATES.items():
        row = await session.scalar(select(AgentLesson).where(AgentLesson.project_id == run.project_id,
            AgentLesson.tenant_id == run.tenant_id, AgentLesson.tool == action.tool,
            AgentLesson.category == category, AgentLesson.workflow_version == WORKFLOW_VERSION))
        if row and row.verified:
            continue
        validator = VALIDATORS.get(category)
        evidence = await validator(session, run, action) if validator else None
        if row is None:
            row = AgentLesson(tenant_id=run.tenant_id, project_id=run.project_id, run_id=run.id, tool=action.tool,
                category=category, workflow_version=WORKFLOW_VERSION, advice=advice)
            session.add(row)
        if evidence:
            row.run_id, row.evidence, row.verified = run.id, evidence, True


async def recall(session, run, tool):
    if not get_settings().agent_experience_enabled:
        return []
    rows = list(await session.scalars(select(AgentLesson).where(AgentLesson.tenant_id == run.tenant_id,
        AgentLesson.project_id == run.project_id, AgentLesson.tool == tool, AgentLesson.verified.is_(True),
        AgentLesson.workflow_version == WORKFLOW_VERSION).order_by(AgentLesson.id).limit(3)))
    # Stored arbitrary prose never enters context, even if DB data were malformed.
    return [MemoryEntry(id=r.id, advice=TEMPLATES[r.category], source_run_id=r.run_id,
        source_action_id=r.evidence["action_ids"][0], verified=True) for r in rows
        if r.category in VALIDATORS and r.category in TEMPLATES and r.evidence.get("action_ids")]
