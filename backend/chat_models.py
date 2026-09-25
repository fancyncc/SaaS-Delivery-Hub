"""Private, workspace-scoped conversations and user-managed memory."""
from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.models import Base, uid


class ChatConversation(Base):
    __tablename__ = "chat_conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("implementation_projects.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(120), default="新对话")
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    unread: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    version: Mapped[int] = mapped_column(Integer, default=1)
    messages: Mapped[list] = mapped_column(JSON, default=list)
    documents: Mapped[list] = mapped_column(JSON, default=list)


class ChatMemory(Base):
    __tablename__ = "chat_memories"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)


class ChatContextSnapshot(Base):
    __tablename__ = "chat_context_snapshots"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("chat_conversations.id", ondelete="CASCADE"), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    processed_turn: Mapped[int] = mapped_column(Integer, default=0)
    summary_until: Mapped[int] = mapped_column(Integer, default=0)
    source_digest: Mapped[str] = mapped_column(String(64), default="")
    documents_digest: Mapped[str] = mapped_column(String(64), default="")
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[list] = mapped_column(JSON, default=list)
    generation: Mapped[int] = mapped_column(Integer, default=1)
    mode: Mapped[str] = mapped_column(String(24), default="extractive")
    producer_version: Mapped[str] = mapped_column(String(32), default="context-v1")


class ChatContextTask(Base):
    __tablename__ = "chat_context_tasks"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("chat_conversations.id", ondelete="CASCADE"), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    target_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt: Mapped[float] = mapped_column(Float, default=0)
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    lease_token: Mapped[str] = mapped_column(String(36), default="")
    error_code: Mapped[str] = mapped_column(String(40), default="")


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (UniqueConstraint("conversation_id", "turn", "role"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("chat_conversations.id", ondelete="CASCADE"), index=True)
    turn: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    # Complete turn metadata makes migration lossless and keeps response compatibility.
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    embedding_identity: Mapped[str] = mapped_column(String(200), default="")


class ChatHistoryMigration(Base):
    __tablename__ = "chat_history_migrations"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("chat_conversations.id", ondelete="CASCADE"), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    source_digest: Mapped[str] = mapped_column(String(64))
    turn_count: Mapped[int] = mapped_column(Integer)
    activated: Mapped[bool] = mapped_column(Boolean, default=False)


class MemoryItem(Base):
    __tablename__ = "chat_memory_items"
    __table_args__ = (UniqueConstraint("user_id", "scope", "scope_id", "key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("customer_tenants.id"), nullable=True, index=True)
    scope: Mapped[str] = mapped_column(String(20), default="workspace")
    scope_id: Mapped[str] = mapped_column(String(36))
    category: Mapped[str] = mapped_column(String(24))
    key: Mapped[str] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="active")
    version: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class MemoryPreference(Base):
    __tablename__ = "chat_memory_preferences"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    auto_extract: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)


class MemoryCandidate(Base):
    __tablename__ = "chat_memory_candidates"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "fingerprint"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("chat_conversations.id", ondelete="CASCADE"), index=True)
    source_message_id: Mapped[str] = mapped_column(String(36))
    fingerprint: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(24))
    key: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    version: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[float] = mapped_column(Float)
    memory_item_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
