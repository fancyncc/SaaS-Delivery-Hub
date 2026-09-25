"""Conservative, evidence-linked reflection; experience never proves capability."""
from sqlalchemy import select

from backend.agent_models import AgentAction, Experience
from backend.agent_types import MemoryEntry
from backend.delivery import digest


async def reflect(session, run, action: AgentAction):
    # Only allowlisted diagnoses pass the gate, never exception text or PII.
    code = action.result.get("error_code", "tool_error")
    advice = {
        "timeout": "工具超时后先核对执行凭证，再以相同业务输入重试。",
        "unavailable": "依赖不可用时检查服务配置，恢复后重新验证工具结果。",
        "invalid_output": "结构化输出未通过校验时依据原始需求和知识引用重新生成。",
    }.get(code)
    if not advice or action.status != "failed":
        return
    fingerprint = digest({"tool": action.tool, "advice": advice})
    existing = await session.scalar(select(Experience).where(Experience.project_id == run.project_id, Experience.tenant_id == run.tenant_id, Experience.fingerprint == fingerprint))
    if not existing:
        session.add(Experience(tenant_id=run.tenant_id, project_id=run.project_id, run_id=run.id,
            source_action_id=action.id, tool=action.tool, advice=advice, fingerprint=fingerprint))


async def verify_recovery(session, run, action: AgentAction):
    from backend.agent_lessons import record
    await record(session, run, action)
    if action.status != "succeeded":
        return
    rows = (await session.scalars(select(Experience).where(Experience.tenant_id == run.tenant_id,
        Experience.project_id == run.project_id, Experience.run_id == run.id,
        Experience.tool == action.tool, Experience.verified.is_(False), Experience.active.is_(True)))).all()
    for row in rows:
        failed = await session.get(AgentAction, row.source_action_id)
        if failed and failed.request_digest == action.request_digest and failed.milestone_id == action.milestone_id:
            row.verified = True
            row.verification_action_id = action.id


async def recall(session, run, tool: str) -> list[MemoryEntry]:
    rows = (await session.scalars(select(Experience).where(Experience.tenant_id == run.tenant_id,
        Experience.project_id == run.project_id, Experience.tool == tool,
        Experience.verified.is_(True), Experience.active.is_(True)).order_by(Experience.created_at.desc()).limit(3))).all()
    from backend.agent_lessons import recall as recall_lessons
    return [MemoryEntry(id=r.id, advice=r.advice, source_run_id=r.run_id,
        source_action_id=r.source_action_id, verified=True) for r in rows] + await recall_lessons(session, run, tool)
