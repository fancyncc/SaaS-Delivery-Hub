"""Isolated experimental structure index. No changes to legacy vectors."""
import sqlalchemy as sa

from alembic import op
from backend.vector_type import V3Vector

revision='0022_rag_v3'
down_revision='0021_retrieval_sources'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('rag_v3_documents',
        sa.Column('id',sa.String(36),primary_key=True),sa.Column('tenant_id',sa.String(36),sa.ForeignKey('customer_tenants.id'),nullable=False),
        sa.Column('origin_id',sa.String(36),sa.ForeignKey('knowledge_documents.id'),nullable=False),sa.Column('filename',sa.String(200),nullable=False),
        sa.Column('raw',sa.LargeBinary(),nullable=False),sa.Column('options',sa.JSON(),nullable=False),sa.Column('phase',sa.String(24),nullable=False),
        sa.Column('error',sa.String(300),nullable=False),sa.Column('warnings',sa.JSON(),nullable=False),sa.Column('digest',sa.String(64),nullable=False),
        sa.Column('identity',sa.String(200),nullable=False),sa.Column('attempts',sa.Integer(),nullable=False),sa.UniqueConstraint('tenant_id','origin_id'))
    op.create_table('rag_v3_nodes',sa.Column('id',sa.String(80),primary_key=True),sa.Column('tenant_id',sa.String(36),sa.ForeignKey('customer_tenants.id'),nullable=False),
        sa.Column('document_id',sa.String(36),sa.ForeignKey('rag_v3_documents.id',ondelete='CASCADE'),nullable=False),sa.Column('structure',sa.JSON(),nullable=False))
    op.create_table('rag_v3_units',sa.Column('id',sa.String(100),primary_key=True),sa.Column('tenant_id',sa.String(36),sa.ForeignKey('customer_tenants.id'),nullable=False),
        sa.Column('document_id',sa.String(36),sa.ForeignKey('rag_v3_documents.id',ondelete='CASCADE'),nullable=False),sa.Column('node_id',sa.String(80),nullable=False),
        sa.Column('ordinal',sa.Integer(),nullable=False),sa.Column('text',sa.Text(),nullable=False),sa.Column('heading',sa.Text(),nullable=False),
        sa.Column('kind',sa.String(30),nullable=False),sa.Column('location',sa.JSON(),nullable=False),sa.Column('parent',sa.String(80)),sa.Column('record',sa.String(200)),
        sa.Column('tokens',sa.Integer(),nullable=False),sa.Column('lexemes',sa.Text(),nullable=False),sa.Column('identifiers',sa.JSON(),nullable=False),sa.Column('embedding',V3Vector()))
    for name in ['rag_v3_documents','rag_v3_nodes','rag_v3_units']:
        op.create_index('ix_'+name+'_tenant_id',name,['tenant_id'])
        if name!='rag_v3_documents':
            op.create_index('ix_'+name+'_document_id',name,['document_id'])
        if op.get_bind().dialect.name=='postgresql':
            op.execute(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY")
            op.execute(f"CREATE POLICY tenant_isolation ON {name} USING (tenant_id=current_setting('app.current_tenant_id',true)) WITH CHECK (tenant_id=current_setting('app.current_tenant_id',true))")



def downgrade():
    for name in ['rag_v3_units','rag_v3_nodes','rag_v3_documents']:
        op.drop_table(name)
