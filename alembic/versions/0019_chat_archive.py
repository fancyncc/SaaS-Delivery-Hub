"""Persistent conversation archive state."""
import sqlalchemy as sa

from alembic import op

revision = "0019_chat_archive"
down_revision = "0018_knowledge_scope"
branch_labels = None
depends_on = None


def upgrade():
    if "archived" not in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('chat_conversations')}:
        op.add_column('chat_conversations', sa.Column('archived', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table('chat_conversations') as batch:
        batch.drop_column('archived')
