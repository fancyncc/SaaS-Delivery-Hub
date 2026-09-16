"""Project visibility for company knowledge."""
import sqlalchemy as sa

from alembic import op

revision = "0018_knowledge_scope"
down_revision = "0017_chat"
branch_labels = None
depends_on = None


def upgrade():
    if "project_id" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("knowledge_documents")}:
        with op.batch_alter_table("knowledge_documents") as batch:
            batch.add_column(sa.Column("project_id", sa.String(36), nullable=True))
            batch.create_foreign_key("fk_knowledge_project", "implementation_projects", ["project_id"], ["id"])
            batch.create_index("ix_knowledge_documents_project_id", ["project_id"])


def downgrade():
    with op.batch_alter_table("knowledge_documents") as batch:
        batch.drop_index("ix_knowledge_documents_project_id")
        batch.drop_column("project_id")
