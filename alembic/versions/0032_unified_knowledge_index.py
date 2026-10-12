"""Use plain names for the shared knowledge index; preserve rows and constraints."""
import sqlalchemy as sa

from alembic import op

revision = "0032_unified_knowledge_index"
down_revision = "0031_conversation_tools"
branch_labels = None
depends_on = None

TABLES = ("documents", "nodes", "units")


def rename(old: str, new: str) -> None:
    for suffix in TABLES:
        before, after = old + suffix, new + suffix
        indexes = sa.inspect(op.get_bind()).get_indexes(before)
        op.rename_table(before, after)
        for index in indexes:
            if index["name"].startswith("ix_" + before):
                op.drop_index(index["name"], table_name=after)
                op.create_index(index["name"].replace(before, after, 1), after,
                                index["column_names"], unique=index["unique"])


def upgrade() -> None:
    rename("rag_v3_", "retrieval_")
    op.execute(sa.text("UPDATE knowledge_documents SET index_status = 'indexed_' || "
                       "substr(index_status, 4) WHERE substr(index_status, 1, 3) = 'v3_'"))


def downgrade() -> None:
    op.execute(sa.text("UPDATE knowledge_documents SET index_status = 'v3_' || "
                       "substr(index_status, 9) WHERE substr(index_status, 1, 8) = 'indexed_'"))
    rename("retrieval_", "rag_v3_")
