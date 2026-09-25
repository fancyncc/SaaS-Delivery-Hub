"""Durable private context snapshots and maintenance outbox."""
import sqlalchemy as sa

from alembic import op

revision = "0026_chat_context"
down_revision = "0025_chat_organization"
branch_labels = None
depends_on = None


def upgrade():
    for name, columns in (
        ("chat_context_snapshots", [
            sa.Column("processed_turn", sa.Integer(), nullable=False),
            sa.Column("summary_until", sa.Integer(), nullable=False),
            sa.Column("source_digest", sa.String(64), nullable=False),
            sa.Column("documents_digest", sa.String(64), nullable=False),
            sa.Column("state", sa.JSON(), nullable=False),
            sa.Column("summary", sa.JSON(), nullable=False),
            sa.Column("generation", sa.Integer(), nullable=False),
            sa.Column("mode", sa.String(24), nullable=False),
            sa.Column("producer_version", sa.String(32), nullable=False),
        ]),
        ("chat_context_tasks", [
            sa.Column("target_version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("next_attempt", sa.Float(), nullable=False),
            sa.Column("lease_until", sa.Float(), nullable=False),
            sa.Column("lease_token", sa.String(36), nullable=False),
            sa.Column("error_code", sa.String(40), nullable=False),
        ]),
    ):
        # The legacy bootstrap migration uses current Base.metadata.create_all.
        # Fresh installations may already have these tables; upgrades do not.
        existing = sa.inspect(op.get_bind()).has_table(name)
        if not existing:
            op.create_table(name,
                sa.Column("conversation_id", sa.String(36), sa.ForeignKey("chat_conversations.id", ondelete="CASCADE"), primary_key=True),
                sa.Column("tenant_id", sa.String(36), sa.ForeignKey("customer_tenants.id"), nullable=False),
                sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
                *columns)
            for column in ("tenant_id", "user_id"):
                op.create_index(f"ix_{name}_{column}", name, [column])
        if op.get_bind().dialect.name == "postgresql":
            op.execute(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY")
            op.execute(f"CREATE POLICY tenant_isolation ON {name} USING (tenant_id = current_setting('app.current_tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))")
    indexes = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("chat_context_tasks")}
    if "ix_chat_context_tasks_status" not in indexes:
        op.create_index("ix_chat_context_tasks_status", "chat_context_tasks", ["status"])


def downgrade():
    op.drop_table("chat_context_tasks")
    op.drop_table("chat_context_snapshots")
