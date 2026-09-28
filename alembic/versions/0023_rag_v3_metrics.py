"""V3 adjacency and parsing/index duration diagnostics."""
import sqlalchemy as sa

from alembic import op

revision='0023_rag_v3_metrics'
down_revision='0022_rag_v3'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('rag_v3_documents',sa.Column('metrics',sa.JSON(),nullable=False,server_default='{}'))
    op.add_column('rag_v3_units',sa.Column('previous_id',sa.String(100),nullable=True))
    op.add_column('rag_v3_units',sa.Column('next_id',sa.String(100),nullable=True))
    op.create_index('ix_rag_v3_documents_origin_id','rag_v3_documents',['origin_id'])
    op.create_index('ix_rag_v3_documents_phase','rag_v3_documents',['phase'])


def downgrade():
    op.drop_index('ix_rag_v3_documents_phase',table_name='rag_v3_documents')
    op.drop_index('ix_rag_v3_documents_origin_id',table_name='rag_v3_documents')
    op.drop_column('rag_v3_units','next_id')
    op.drop_column('rag_v3_units','previous_id')
    op.drop_column('rag_v3_documents','metrics')
