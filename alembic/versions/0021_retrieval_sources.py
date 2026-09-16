"""Separate enterprise, project and private source indexes with versioned jobs."""
from alembic import op
from backend.retrieval_sources_models import RetrievalBase

revision = "0021_retrieval_sources"
down_revision = "0020_bge_cpu"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    RetrievalBase.metadata.create_all(bind, checkfirst=True)
    if bind.dialect.name == "postgresql":
        for name in ("retrieval_sources", "retrieval_source_chunks"):
            op.execute(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY")
            op.execute(f"CREATE POLICY tenant_isolation ON {name} USING (tenant_id = current_setting('app.current_tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))")
        op.execute("CREATE INDEX ix_retrieval_source_chunks_vector ON retrieval_source_chunks USING hnsw (embedding vector_cosine_ops)")


def downgrade():
    RetrievalBase.metadata.drop_all(op.get_bind())
