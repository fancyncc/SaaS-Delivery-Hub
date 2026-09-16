"""Durable agent loop and versioned chunk indexes."""
import sqlalchemy as sa

from alembic import op
from backend.agent_models import AgentAction, Experience, KnowledgePiece
from backend.vector_type import Vector1024

revision = "0016_agent_rag"
down_revision = "0015_username_profile"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        version = bind.execute(sa.text("SELECT extversion FROM pg_extension WHERE extname='vector'")).scalar()
        if not version or tuple(int(part) for part in version.split(".")[:2]) < (0, 8):
            raise RuntimeError("Agent RAG requires pgvector >= 0.8; upgrade the extension first")
    columns = {c["name"] for c in sa.inspect(bind).get_columns("knowledge_documents")}
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_constraint("ck_agent_run_status", type_="check")
        batch.create_check_constraint("ck_agent_run_status", "status IN ('pending','running','preparing_materials','waiting_approval','succeeded','failed','cancelled','blocked')")
    for column in [sa.Column("index_status", sa.String(20), nullable=False, server_default="pending"),
                   sa.Column("index_attempts", sa.Integer(), nullable=False, server_default="0"),
                   sa.Column("index_error", sa.String(100), nullable=False, server_default=""),
                   sa.Column("index_version", sa.String(160), nullable=False, server_default="")]:
        if column.name not in columns:
            op.add_column("knowledge_documents", column)
    if "ix_knowledge_documents_index_status" not in {i["name"] for i in sa.inspect(bind).get_indexes("knowledge_documents")}:
        op.create_index("ix_knowledge_documents_index_status", "knowledge_documents", ["index_status"])
    # Freeze the historical dimension instead of inheriting the current ORM type.
    chunks = KnowledgePiece.__table__.to_metadata(sa.MetaData())
    chunks.c.embedding.type = Vector1024()
    # Resolve foreign keys against the same metadata without creating source tables.
    for name in ("customer_tenants", "knowledge_documents"):
        KnowledgePiece.metadata.tables[name].to_metadata(chunks.metadata)
    for table in [chunks, AgentAction.__table__, Experience.__table__]:
        table.create(bind, checkfirst=True)
        if bind.dialect.name == "postgresql":
            op.execute(f"ALTER TABLE {table.name} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table.name} FORCE ROW LEVEL SECURITY")
            op.execute(f"CREATE POLICY tenant_isolation ON {table.name} USING (tenant_id = current_setting('app.current_tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))")
    if bind.dialect.name == "postgresql":
        op.execute("CREATE INDEX ix_knowledge_chunks_fts ON knowledge_chunks USING gin (to_tsvector('simple', lexemes))")
        op.execute("CREATE INDEX ix_knowledge_chunks_vector ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)")


def downgrade():
    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT count(*) FROM agent_runs WHERE status='blocked'")).scalar()
    if count:
        raise RuntimeError("Resolve or cancel blocked Runs before downgrade")
    for table in [Experience.__table__, AgentAction.__table__, KnowledgePiece.__table__]:
        table.drop(bind)
    op.drop_index("ix_knowledge_documents_index_status", table_name="knowledge_documents")
    for name in ("index_version", "index_error", "index_attempts", "index_status"):
        op.drop_column("knowledge_documents", name)
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_constraint("ck_agent_run_status", type_="check")
        batch.create_check_constraint("ck_agent_run_status", "status IN ('pending','running','preparing_materials','waiting_approval','succeeded','failed','cancelled')")
