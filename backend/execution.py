"""Transactional API-to-worker handoff. No broker call happens in API transactions."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.models import AgentRun, WorkflowOutbox


async def dispatch(session: AsyncSession, run: AgentRun) -> AgentRun:
    if get_settings().execution_mode == "inline":
        from backend.workflow import advance
        return await advance(session, run)
    existing = await session.scalar(select(WorkflowOutbox).where(WorkflowOutbox.run_id == run.id, WorkflowOutbox.run_version == run.version))
    if not existing:
        from backend.observability import trace_carrier
        session.add(WorkflowOutbox(run_id=run.id, tenant_id=run.tenant_id, actor_id=run.started_by,
                                  run_version=run.version, trace_context=trace_carrier()))
    await session.flush()
    return run
