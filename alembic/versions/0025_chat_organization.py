"""Persist private conversation pin and unread state."""
import sqlalchemy as sa

from alembic import op

revision = '0025_chat_organization'
down_revision = '0024_single_active_run'
branch_labels = None
depends_on = None


def upgrade():
    existing = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('chat_conversations')}
    if 'pinned' not in existing:
        op.add_column('chat_conversations', sa.Column('pinned', sa.Boolean(), nullable=False, server_default=sa.false()))
    if 'unread' not in existing:
        op.add_column('chat_conversations', sa.Column('unread', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column('chat_conversations', 'unread')
    op.drop_column('chat_conversations', 'pinned')
