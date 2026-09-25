"""Durable action journal, isolated experience and chunk indexes."""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.models import Base, uid, utcnow
from backend.vector_type import Vector512


class KnowledgePiece(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (UniqueConstraint("document_id", "index_version", "ordinal"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text)
    lexemes: Mapped[str] = mapped_column(Text)
    index_version: Mapped[str] = mapped_column(String(160))
    embedding_model: Mapped[str] = mapped_column(String(120), default="")
    embedding: Mapped[list | None] = mapped_column(Vector512(), nullable=True)


class AgentAction(Base):
    __tablename__ = "agent_actions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    stage: Mapped[str] = mapped_column(String(80))
    milestone_id: Mapped[str] = mapped_column(String(80))
    tool: Mapped[str] = mapped_column(String(80))
    request_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Experience(Base):
    __tablename__ = "agent_memories"
    __table_args__ = (UniqueConstraint("project_id", "fingerprint"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("implementation_projects.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    source_action_id: Mapped[str] = mapped_column(ForeignKey("agent_actions.id"))
    verification_action_id: Mapped[str | None] = mapped_column(ForeignKey("agent_actions.id"), nullable=True)
    tool: Mapped[str] = mapped_column(String(80))
    advice: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentLesson(Base):
    __tablename__ = "agent_lessons"
    __table_args__ = (UniqueConstraint("project_id", "tool", "category", "workflow_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("implementation_projects.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    tool: Mapped[str] = mapped_column(String(80))
    category: Mapped[str] = mapped_column(String(32))
    workflow_version: Mapped[str] = mapped_column(String(40))
    advice: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
