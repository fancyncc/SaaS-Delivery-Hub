"""multi-tenant identity, tenant backfill, and soft deletion

Revision ID: 0001_multitenant
Revises:
Create Date: 2026-08-23
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from backend.models import Base

revision = "0001_multitenant"
down_revision = None
branch_labels = None
depends_on = None

LEGACY_TENANT_ID = "00000000-0000-0000-0000-000000000001"
TENANT_TABLES = [
    "implementation_projects", "project_documents", "agent_runs", "agent_steps",
    "approval_requests", "idempotency_records", "import_jobs", "audit_events", "evaluation_runs",
]

# The bootstrap migration reads today's ORM metadata. Tables owned by later
# migrations must stay out of a fresh database until their revision runs.
LATER_TABLES = {
    "knowledge_chunks", "agent_actions", "agent_memories",
    "chat_conversations", "chat_memories",
    "chat_context_snapshots", "chat_context_tasks", "chat_messages",
    "chat_history_migrations", "chat_memory_items", "chat_memory_preferences",
    "chat_memory_candidates", "rag_v3_documents",
    "rag_v3_nodes", "rag_v3_units", "project_tasks",
    "project_task_documents", "project_task_reviews",
}


def _columns(inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    # Creates all new identity tables on legacy databases and the complete schema on fresh databases.
    # Agent tables require the pgvector extension installed by migration 0012.
    # Do not let the current ORM metadata create future vector tables early.
    Base.metadata.create_all(bind=bind, tables=[table for table in Base.metadata.sorted_tables
        if table.name not in LATER_TABLES])
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    for table in TENANT_TABLES:
        if table in tables and "tenant_id" not in _columns(inspector, table):
            op.add_column(table, sa.Column("tenant_id", sa.String(36), nullable=True))
            inspector = sa.inspect(bind)

    project_columns = _columns(inspector, "implementation_projects")
    additions = {
        "created_by": sa.Column("created_by", sa.String(36), nullable=True),
        "owner_user_id": sa.Column("owner_user_id", sa.String(36), nullable=True),
        "deleted_at": sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        "deleted_by": sa.Column("deleted_by", sa.String(36), nullable=True),
    }
    for name, column in additions.items():
        if name not in project_columns:
            op.add_column("implementation_projects", column)

    audit_columns = _columns(sa.inspect(bind), "audit_events")
    audit_additions = {
        "actor_user_id": sa.Column("actor_user_id", sa.String(36), nullable=True),
        "request_id": sa.Column("request_id", sa.String(64), nullable=False, server_default=""),
        "ip_hash": sa.Column("ip_hash", sa.String(64), nullable=False, server_default=""),
        "outcome": sa.Column("outcome", sa.String(32), nullable=False, server_default="success"),
    }
    for name, column in audit_additions.items():
        if name not in audit_columns:
            op.add_column("audit_events", column)

    tenant_columns = _columns(sa.inspect(bind), "customer_tenants")
    if "deleted_by" not in tenant_columns:
        op.add_column("customer_tenants", sa.Column("deleted_by", sa.String(36), nullable=True))

    exists = bind.execute(sa.text("SELECT id FROM customer_tenants WHERE slug = 'legacy-demo'")).first()
    if not exists:
        bind.execute(sa.text(
            "INSERT INTO customer_tenants (id, name, slug, status, settings, created_at, updated_at) "
            "VALUES (:id, 'Legacy Demo', 'legacy-demo', 'active', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ), {"id": LEGACY_TENANT_ID})
    else:
        legacy_id = exists[0]
        if legacy_id:
            globals()["LEGACY_TENANT_ID"] = legacy_id

    for table in TENANT_TABLES:
        bind.execute(sa.text(f"UPDATE {table} SET tenant_id = :tenant_id WHERE tenant_id IS NULL"), {"tenant_id": LEGACY_TENANT_ID})

    if bind.dialect.name == "postgresql":
        for table in TENANT_TABLES:
            op.alter_column(table, "tenant_id", existing_type=sa.String(36), nullable=False)
        # Existing installations used a global (scope,key) uniqueness rule.
        for constraint in sa.inspect(bind).get_unique_constraints("idempotency_records"):
            if set(constraint.get("column_names") or []) == {"scope", "key"} and constraint.get("name"):
                op.drop_constraint(constraint["name"], "idempotency_records", type_="unique")
        constraints = sa.inspect(bind).get_unique_constraints("idempotency_records")
        if not any(set(x.get("column_names") or []) == {"tenant_id", "scope", "key"} for x in constraints):
            op.create_unique_constraint("uq_idempotency_tenant_scope_key", "idempotency_records", ["tenant_id", "scope", "key"])

    inspector = sa.inspect(bind)
    for table in TENANT_TABLES:
        index_name = f"ix_{table}_tenant_id"
        if not any(index["name"] == index_name for index in inspector.get_indexes(table)):
            op.create_index(index_name, table, ["tenant_id"])


def downgrade() -> None:
    raise RuntimeError("This governance migration is intentionally irreversible")
