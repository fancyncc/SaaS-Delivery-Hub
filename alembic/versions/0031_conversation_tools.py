"""Versioned optional tool budgets and private evidence on existing conversations."""
import sqlalchemy as sa

from alembic import op

revision = "0031_conversation_tools"
down_revision = "0030_execution_safety"
branch_labels = None
depends_on = None


def upgrade():
    # Early bootstrap migrations create tables from current ORM metadata.
    # Existing deployments still need both columns; empty installs may have them.
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("chat_conversations")}
    if "tool_state" not in columns:
        op.add_column("chat_conversations", sa.Column("tool_state", sa.JSON(), nullable=False, server_default="{}"))
    if "tool_state_version" not in columns:
        op.add_column("chat_conversations", sa.Column("tool_state_version", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("chat_conversations", "tool_state_version")
    op.drop_column("chat_conversations", "tool_state")
