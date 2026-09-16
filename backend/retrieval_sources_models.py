"""Derived retrieval data, separate metadata to keep historical migrations frozen."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from backend.models import Project, Tenant, User, uid, utcnow
from backend.vector_type import Vector512


class RetrievalBase(DeclarativeBase):
    pass


class RetrievalSource(RetrievalBase):
    __tablename__ = "retrieval_sources"
    __table_args__ = (UniqueConstraint("tenant_id", "kind", "origin_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey(Tenant.__table__.c.id), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    origin_id: Mapped[str] = mapped_column(String(80))
    project_id: Mapped[str | None] = mapped_column(ForeignKey(Project.__table__.c.id, ondelete="CASCADE"), index=True)
    # Keep an inactive tombstone long enough for asynchronous search cleanup.
    conversation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    owner_id: Mapped[str | None] = mapped_column(ForeignKey(User.__table__.c.id, ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(String(80), default="1")
    digest: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    phase: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    generation: Mapped[int] = mapped_column(Integer, default=1)
    indexed_generation: Mapped[int] = mapped_column(Integer, default=0)
    index_identity: Mapped[str] = mapped_column(String(160), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str] = mapped_column(String(64), default="")
    chunks_total: Mapped[int] = mapped_column(Integer, default=0)
    chunks_done: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class RetrievalChunk(RetrievalBase):
    __tablename__ = "retrieval_source_chunks"
    __table_args__ = (UniqueConstraint("source_id", "generation", "ordinal"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    source_id: Mapped[str] = mapped_column(ForeignKey(RetrievalSource.id, ondelete="CASCADE"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey(Tenant.__table__.c.id), index=True)
    generation: Mapped[int] = mapped_column(Integer)
    ordinal: Mapped[int] = mapped_column(Integer)
    citation_id: Mapped[str] = mapped_column(String(160))
    heading: Mapped[str] = mapped_column(Text, default="")
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body: Mapped[str] = mapped_column(Text)
    lexemes: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list | None] = mapped_column(Vector512(), nullable=True)
