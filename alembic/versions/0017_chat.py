"""Private RAG conversations and explicit user memory."""
from alembic import op
from backend.chat_models import ChatConversation, ChatMemory

revision = "0017_chat"
down_revision = "0016_agent_rag"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    for table in (ChatConversation.__table__, ChatMemory.__table__):
        table.create(bind, checkfirst=True)
        if bind.dialect.name == "postgresql":
            op.execute(f"ALTER TABLE {table.name} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table.name} FORCE ROW LEVEL SECURITY")
            op.execute(f"CREATE POLICY tenant_isolation ON {table.name} USING (tenant_id = current_setting('app.current_tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))")


def downgrade():
    for table in (ChatMemory.__table__, ChatConversation.__table__):
        table.drop(op.get_bind())
