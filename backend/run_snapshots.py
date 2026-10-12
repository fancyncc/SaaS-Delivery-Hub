"""Append-only execution inputs and approval revisions, scoped to a Run.

Legacy runs remain explicitly unsnapshotted. Runtime authorization is always live.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    event,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from backend.config import get_settings, retrieval_path
from backend.models import AgentRun, Base, Project, ProjectDocument, uid, utcnow


def checksum(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


class RunSnapshot(Base):
    __tablename__ = "run_snapshots"
    __table_args__ = (UniqueConstraint("run_id", "sequence"),
        Index("uq_run_snapshot_input", "run_id", unique=True,
              postgresql_where=text("kind = 'input'"), sqlite_where=text("kind = 'input'")))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("implementation_projects.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


@event.listens_for(RunSnapshot, "before_update")
def immutable_snapshot(mapper, connection, target):
    raise ValueError("Run snapshots are immutable; append a new revision")


@event.listens_for(RunSnapshot.__table__, "after_create")
def sqlite_immutability(table, connection, **kwargs):
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql("CREATE TRIGGER IF NOT EXISTS run_snapshots_no_update "
                                   "BEFORE UPDATE ON run_snapshots BEGIN "
                                   "SELECT RAISE(ABORT, 'immutable run snapshot'); END")
        connection.exec_driver_sql("CREATE TRIGGER IF NOT EXISTS run_snapshots_no_delete "
            "BEFORE DELETE ON run_snapshots WHEN NOT EXISTS (SELECT 1 FROM implementation_projects p "
            "WHERE p.id = OLD.project_id AND datetime(p.deleted_at) <= datetime('now', '-30 days')) "
            "BEGIN SELECT RAISE(ABORT, 'snapshot retention requires expired project'); END")


@event.listens_for(AgentRun.__table__, "after_create")
def sqlite_version_guard(table, connection, **kwargs):
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql("CREATE TRIGGER IF NOT EXISTS agent_runs_snapshot_version "
            "BEFORE UPDATE OF snapshot_version ON agent_runs "
            "WHEN OLD.snapshot_version > 0 AND NEW.snapshot_version <> OLD.snapshot_version "
            "BEGIN SELECT RAISE(ABORT, 'immutable snapshot version'); END")


@event.listens_for(AgentRun, "before_update")
def immutable_snapshot_version(mapper, connection, target):
    history = inspect(target).attrs.snapshot_version.history
    if history.deleted and history.deleted[0] and history.has_changes():
        raise ValueError("Cannot remove snapshot protection from an existing Run")


# Only code inside an authorized workflow round may journal private model inputs.
audit_scope: ContextVar[tuple[AsyncSession, AgentRun] | None] = ContextVar("run_audit", default=None)


def runtime_identity() -> dict:
    from backend.intelligence import PROMPT_VERSION

    settings = get_settings()
    fields = ("model_mode", "model_name", "model_api_style", "llm_mode", "llm_local_model",
              "rag_mode", "embedding_mode", "embedding_local_model", "embedding_model",
              "embedding_revision", "embedding_dimensions", "embedding_query_instruction",
              "embedding_query_style", "reranker_mode", "reranker_model", "reranker_local_model",
              "reranker_revision", "knowledge_index_version", "retrieval_relevance_mode")
    identity = {key: getattr(settings, key) for key in fields}
    for key, value in settings.model_dump().items():
        if (key.startswith(("embedding_", "reranker_", "knowledge_"))
                or key.startswith("retrieval_") and key not in {
                    "retrieval_cpu_threads", "retrieval_queue_size", "retrieval_model_cache"}) and "api_key" not in key:
            identity[key] = checksum(value) if key.endswith("base_url") else value
    for key in ("model_max_output_tokens", "model_context_window", "model_tokenizer", "model_tokenizer_map"):
        identity[key] = getattr(settings, key)
    for key in ("retrieval_release", "retrieval_model_profile"):
        path = Path(getattr(settings, key)) if getattr(settings, key) else None
        identity[key + "_file_hash"] = hashlib.sha256(path.read_bytes()).hexdigest() if path and path.is_file() else None
    # Endpoint identity is retained without storing embedded credentials or URL queries.
    for key in ("model_base_url", "llm_local_base_url", "embedding_base_url", "reranker_base_url"):
        identity[key + "_hash"] = checksum(getattr(settings, key))
    identity["prompt_version"] = PROMPT_VERSION
    from backend.prompt_boundary import DATA_BOUNDARY
    identity["prompt_boundary_hash"] = checksum(DATA_BOUNDARY)
    if settings.skill_runtime_enabled or settings.official_web_enabled:
        identity["tool_runtime"] = {"version": "1", "skills": settings.skill_runtime_enabled,
            "skill_max_tokens": settings.skill_max_tokens, "official_web": settings.official_web_enabled,
            "official_web_timeout": settings.official_web_timeout,
            "official_web_max_bytes": settings.official_web_max_bytes,
            "official_policy_hash": checksum(settings.official_products)}
    return identity


def normalize_runtime_identity(value: dict) -> dict:
    """Accept spelling-only changes without modifying immutable historical input."""
    normalized = {}
    for key, item in value.items():
        key = key.replace("rag_v3_", "retrieval_", 1) if key.startswith("rag_v3_") else key
        if key in {"retrieval_release", "retrieval_model_profile"} and isinstance(item, str):
            item = retrieval_path(item)
        normalized[key] = item
    return normalized


async def append(session: AsyncSession, run: AgentRun, kind: str, payload: dict) -> RunSnapshot:
    sequence = await session.scalar(select(func.max(RunSnapshot.sequence)).where(RunSnapshot.run_id == run.id))
    item = RunSnapshot(tenant_id=run.tenant_id, project_id=run.project_id, run_id=run.id,
                       sequence=(sequence or 0) + 1, kind=kind, payload=deepcopy(payload),
                       content_hash=checksum(payload))
    session.add(item)
    await session.flush()
    return item


async def capture(session: AsyncSession, run: AgentRun, project: Project) -> RunSnapshot:
    if run.snapshot_version:
        raise HTTPException(409, "本次执行已经固化输入")
    doc = await session.scalar(select(ProjectDocument).where(ProjectDocument.project_id == project.id))
    payload = {
        "requirements_text": project.requirements_text,
        "project": {"name": project.name, "customer_name": project.customer_name},
        "document": {"id": doc.id, "content": doc.content} if doc else None,
        "runtime": runtime_identity(), "approval_policy": "independent-approval-v1",
    }
    if get_settings().skill_runtime_enabled:
        from backend.skill_runtime import catalog
        payload["skills"] = catalog()
    item = await append(session, run, "input", payload)
    run.snapshot_version = 1
    await session.flush()
    return item


def verify(item: RunSnapshot, run: AgentRun) -> None:
    if (item.run_id != run.id or item.tenant_id != run.tenant_id or item.project_id != run.project_id
            or checksum(item.payload) != item.content_hash):
        raise HTTPException(409, "执行快照范围或完整性校验失败")


async def inputs(session: AsyncSession, run: AgentRun) -> RunSnapshot | None:
    if not run.snapshot_version:
        return None
    item = await session.scalar(select(RunSnapshot).where(RunSnapshot.run_id == run.id,
                                                         RunSnapshot.kind == "input"))
    if item is None:
        raise HTTPException(409, "执行输入快照缺失，禁止继续执行")
    verify(item, run)
    return item


async def requirements(session: AsyncSession, run: AgentRun, project: Project) -> str:
    item = await inputs(session, run)
    return item.payload["requirements_text"] if item else project.requirements_text


async def document(session: AsyncSession, run: AgentRun, project: Project) -> dict:
    item = await inputs(session, run)
    if item:
        doc = item.payload["document"]
        return deepcopy(doc["content"]) if doc else {}
    doc = await session.scalar(select(ProjectDocument).where(ProjectDocument.project_id == project.id))
    return deepcopy(doc.content) if doc else {}


async def seal(session: AsyncSession, run: AgentRun, material: dict) -> dict:
    if not run.snapshot_version:
        return material
    item = await append(session, run, "approval", material)
    return {**material, "revision_id": item.id, "revision_hash": item.content_hash}


async def matches(session: AsyncSession, run: AgentRun, approved: dict, current: dict) -> bool:
    if not run.snapshot_version:
        return approved == current
    item = await session.get(RunSnapshot, approved.get("revision_id")) if approved.get("revision_id") else None
    if item is None or item.kind != "approval":
        return False
    verify(item, run)
    return (item.content_hash == approved.get("revision_hash") and item.payload == current
            and approved == {**current, "revision_id": item.id, "revision_hash": item.content_hash})


async def record(kind: str, payload: dict) -> None:
    scope = audit_scope.get()
    if scope and scope[1].snapshot_version:
        await append(scope[0], scope[1], kind, payload)


@asynccontextmanager
async def execution_scope(session: AsyncSession, run: AgentRun):
    item = await inputs(session, run)
    if item and normalize_runtime_identity(item.payload["runtime"]) != normalize_runtime_identity(runtime_identity()):
        raise HTTPException(409, "模型或检索配置已变化，请恢复原配置或新建执行重新审批")
    token = audit_scope.set((session, run))
    from backend.skill_runtime import selected_skill
    skill_token = selected_skill.set(None)
    try:
        yield
    finally:
        selected_skill.reset(skill_token)
        audit_scope.reset(token)
