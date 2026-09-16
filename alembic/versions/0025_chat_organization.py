"""Persist private conversation pin and unread state."""
from alembic import op
import sqlalchemy as sa

revision = '0025_chat_organization'
down_revision = '0024_single_active_run'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('chat_conversations', sa.Column('pinned', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('chat_conversations', sa.Column('unread', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column('chat_conversations', 'unread')
    op.drop_column('chat_conversations', 'pinned')
