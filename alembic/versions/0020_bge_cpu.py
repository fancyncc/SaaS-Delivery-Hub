"""Rebuild derived knowledge vectors for the 512-dimensional CPU BGE profile."""
import sqlalchemy as sa

from alembic import op

revision = "0020_bge_cpu"
down_revision = "0019_chat_archive"
branch_labels = None
depends_on = None


def rebuild(dimensions):
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Migration owner must be able to update every tenant, including with FORCE RLS.
        # Fail closed instead of leaving some tenants marked ready with obsolete vectors.
        op.execute("SET LOCAL row_security = off")
        op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_vector")
        op.execute(f"ALTER TABLE knowledge_chunks ALTER COLUMN embedding TYPE vector({dimensions}) USING NULL::vector({dimensions})")
        op.execute("CREATE INDEX ix_knowledge_chunks_vector ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)")
    else:
        op.execute("UPDATE knowledge_chunks SET embedding = NULL")
    # Keep source documents and chunk IDs (historical citations); the worker publishes
    # a new generation atomically. Old OpenSearch generations cannot pass SQL validation.
    bind.execute(sa.text("UPDATE knowledge_documents SET index_status='pending', index_attempts=0, index_error='', index_version=''"))


def upgrade():
    rebuild(512)


def downgrade():
    rebuild(1024)
