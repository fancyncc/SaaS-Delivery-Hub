"""Durable context health and independent private history indexing progress."""
import sqlalchemy as sa

from alembic import op

revision = "0033_context_maintenance"
down_revision = "0032_unified_knowledge_index"
branch_labels = None
depends_on = None


def upgrade():
    task_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("chat_context_tasks")}
    for name in ("queued_at", "consumer_seen_at"):
        if name not in task_columns:
            op.add_column("chat_context_tasks", sa.Column(name, sa.Float(), nullable=False, server_default="0"))
    history_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("chat_history_migrations")}
    for name, kind, default in (
        ("append_digest", sa.String(64), ""),
        ("index_identity", sa.String(64), ""), ("index_status", sa.String(24), "pending"),
        ("index_attempts", sa.Integer(), "0"), ("index_next_attempt", sa.Float(), "0"),
        ("index_lease_until", sa.Float(), "0"), ("index_lease_token", sa.String(36), ""),
    ):
        if name not in history_columns:
            op.add_column("chat_history_migrations", sa.Column(name, kind, nullable=False, server_default=default))


def downgrade():
    for name in ("index_lease_token", "index_lease_until", "index_next_attempt", "index_attempts", "index_status", "index_identity", "append_digest"):
        op.drop_column("chat_history_migrations", name)
    for name in ("consumer_seen_at", "queued_at"):
        op.drop_column("chat_context_tasks", name)
