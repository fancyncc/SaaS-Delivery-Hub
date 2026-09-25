from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.chat_models import ChatConversation, MemoryItem
from backend.models import (
    AgentRun,
    Base,
    Project,
    ProjectMembership,
    SaaSWorkspace,
    SupportAccessGrant,
)


async def purge_expired_projects(session: AsyncSession) -> int:
    """Physical cleanup job; schedule daily in the deployment task runner."""
    cutoff = datetime.now(UTC) - timedelta(days=30)
    projects = (await session.scalars(select(Project).where(
        Project.deleted_at.is_not(None), Project.deleted_at <= cutoff
    ))).all()
    for project in projects:
        conversation_ids = list(await session.scalars(select(ChatConversation.id).where(ChatConversation.project_id == project.id)))
        await session.execute(delete(MemoryItem).where(
            ((MemoryItem.scope == "project") & (MemoryItem.scope_id == project.id)) |
            ((MemoryItem.scope == "conversation") & MemoryItem.scope_id.in_(conversation_ids))))
        run_ids = list(await session.scalars(select(AgentRun.id).where(AgentRun.project_id == project.id)))
        workspace_ids = list(await session.scalars(select(SaaSWorkspace.id).where(SaaSWorkspace.project_id == project.id)))
        membership_ids = list(await session.scalars(select(ProjectMembership.id).where(ProjectMembership.project_id == project.id)))
        grant_ids = list(await session.scalars(select(SupportAccessGrant.id).where(SupportAccessGrant.target_project_id == project.id)))
        await session.execute(update(AgentRun).where(AgentRun.project_id == project.id).values(retry_of_run_id=None))
        # Delete only rows explicitly belonging to this expired project, child first.
        for table in reversed(Base.metadata.sorted_tables):
            if table.name == Project.__tablename__:
                continue
            conditions = {"project_id": project.id, "target_project_id": project.id}
            matched = False
            for column, value in conditions.items():
                if column in table.c:
                    await session.execute(delete(table).where(table.c[column] == value))
                    matched = True
                    break
            if matched:
                continue
            for column, values in (("conversation_id", conversation_ids), ("run_id", run_ids), ("workspace_id", workspace_ids), ("membership_id", membership_ids), ("grant_id", grant_ids)):
                if column in table.c and values:
                    await session.execute(delete(table).where(table.c[column].in_(values)))
                    break
        await session.delete(project)
    await session.commit()
    return len(projects)
