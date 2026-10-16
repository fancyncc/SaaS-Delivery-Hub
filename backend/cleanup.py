from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.chat_models import ChatConversation, MemoryItem
from backend.config import get_settings
from backend.knowledge_index_models import DocumentNode, IndexedDocument, RetrievalUnit
from backend.models import (
    AgentRun,
    Base,
    CleanupOutbox,
    KnowledgeDocument,
    Project,
    ProjectArtifact,
)
from backend.retrieval_sources_models import RetrievalChunk, RetrievalSource


async def purge_project_data(session: AsyncSession, project: Project) -> None:
    deleted = project.deleted_at
    if deleted and deleted.tzinfo is None:
        deleted = deleted.replace(tzinfo=UTC)
    if not deleted or deleted > datetime.now(UTC) - timedelta(days=30):
        raise HTTPException(409, "项目需在回收站保留满 30 天后才能物理清理")
    conversation_ids = list(await session.scalars(select(ChatConversation.id).where(ChatConversation.project_id == project.id)))
    await session.execute(delete(MemoryItem).where(
        ((MemoryItem.scope == "project") & (MemoryItem.scope_id == project.id)) |
        ((MemoryItem.scope == "conversation") & MemoryItem.scope_id.in_(conversation_ids))))
    docs = list(await session.scalars(select(KnowledgeDocument.id).where(KnowledgeDocument.project_id == project.id)))
    index_ids = list(await session.scalars(select(IndexedDocument.id).where(IndexedDocument.origin_id.in_(docs))))
    sources = list(await session.scalars(select(RetrievalSource.id).where(or_(
        RetrievalSource.project_id == project.id, RetrievalSource.conversation_id.in_(conversation_ids),
        (RetrievalSource.kind == "knowledge") & RetrievalSource.origin_id.in_(docs)))))
    from backend.storage import key
    object_keys = [key(item) for item in await session.scalars(select(ProjectArtifact).where(ProjectArtifact.project_id == project.id))]
    # This durable tombstone commits with SQL deletion. External failures can be retried.
    session.add(CleanupOutbox(tenant_id=project.tenant_id, payload={
        "index_ids": index_ids, "knowledge_ids": docs, "source_ids": sources,
        "object_keys": object_keys, "storage_backend": get_settings().storage_backend,
        "bucket": get_settings().s3_bucket, "search_index": get_settings().opensearch_index}))
    await session.execute(delete(RetrievalChunk).where(RetrievalChunk.source_id.in_(sources)))
    await session.execute(delete(RetrievalSource).where(RetrievalSource.id.in_(sources)))
    await session.execute(delete(RetrievalUnit).where(RetrievalUnit.document_id.in_(index_ids)))
    await session.execute(delete(DocumentNode).where(DocumentNode.document_id.in_(index_ids)))
    await session.execute(delete(IndexedDocument).where(IndexedDocument.id.in_(index_ids)))
    await session.execute(update(AgentRun).where(AgentRun.project_id == project.id).values(retry_of_run_id=None))
    # Collect descendants BEFORE deleting anything, including indirect FK children.
    owned = {Project.__tablename__: {project.id}}
    tables = list(Base.metadata.sorted_tables)
    for table in tables:
        if table.name == Project.__tablename__ or "id" not in table.c:
            continue
        conditions = [fk.parent.in_(owned[fk.column.table.name]) for fk in table.foreign_keys
                      if fk.column.table.name in owned and owned[fk.column.table.name]]
        if conditions:
            owned[table.name] = set(await session.scalars(select(table.c.id).where(or_(*conditions))))
    for table in reversed(tables):
        if table.name != Project.__tablename__ and owned.get(table.name):
            await session.execute(delete(table).where(table.c.id.in_(owned[table.name])))
    await session.delete(project)


async def purge_expired_projects(session: AsyncSession, tenant_id: str | None = None) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=30)
    query = select(Project).where(Project.deleted_at.is_not(None), Project.deleted_at <= cutoff)
    if tenant_id:
        query = query.where(Project.tenant_id == tenant_id)
    projects = list(await session.scalars(query.order_by(Project.deleted_at, Project.id).limit(10).with_for_update(skip_locked=True)))
    for project in projects:
        await purge_project_data(session, project)
    await session.commit()
    return len(projects)


async def drain_cleanup(session: AsyncSession) -> int:
    rows = list(await session.scalars(select(CleanupOutbox).where(CleanupOutbox.status == "pending",
        CleanupOutbox.next_attempt <= time.time()).limit(4).with_for_update(skip_locked=True)))
    for row in rows:
        try:
            async with asyncio.timeout(40):
                payload = row.payload
                if get_settings().opensearch_url:
                    from backend.lexical_index import client
                    async with client() as http:
                        base = payload["search_index"]
                        for index, field, values in ((base, "document_id", payload["knowledge_ids"]),
                            (base + "-v3", "document_id", payload["index_ids"]),
                            (base + "-sources", "source_id", payload["source_ids"])):
                            if not values:
                                continue
                            response = await http.post(f"/{index}/_delete_by_query?refresh=true&conflicts=proceed",
                                json={"query": {"terms": {field: values}}})
                            if response.status_code != 404:
                                response.raise_for_status()
                                result = response.json()
                                if result.get("failures") or result.get("timed_out"):
                                    raise RuntimeError("Partial search cleanup")
                elif get_settings().rag_mode == "real" and any(payload[k] for k in ("index_ids", "knowledge_ids", "source_ids")):
                    raise RuntimeError("Search cleanup configuration missing")
                if payload["storage_backend"] == "s3":
                    from backend.storage import client as storage_client
                    for object_key in payload["object_keys"]:
                        await asyncio.to_thread(storage_client().delete_object, Bucket=payload["bucket"], Key=object_key)
                row.status, row.error = "done", ""
        except Exception as exc:
            row.attempts += 1
            row.error = type(exc).__name__
            row.next_attempt = time.time() + min(86400, 30 * 2 ** min(row.attempts, 12))
    await session.commit()
    return len(rows)
