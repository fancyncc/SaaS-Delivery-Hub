"""Task documents and approval-gated state workflow."""
import sqlalchemy as sa

from alembic import op

revision = "0029_task_review_workflow"
down_revision = "0028_project_collaboration"
branch_labels = None
depends_on = None


def upgrade():
    # Results marked done before this workflow had no review evidence. Return
    # them to active work so they can be submitted and approved explicitly.
    op.execute("UPDATE project_tasks SET status = 'in_progress' WHERE status = 'done'")
    with op.batch_alter_table("project_tasks") as batch:
        batch.drop_constraint("ck_project_task_status", type_="check")
        batch.create_check_constraint("ck_project_task_status",
            "status IN ('todo','in_progress','blocked','pending_review','changes_requested','done')")
        batch.add_column(sa.Column("blocking_reason", sa.Text(), nullable=False, server_default=""))
    op.create_table("project_task_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("customer_tenants.id"), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("implementation_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("project_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("knowledge_documents.id"), nullable=False),
        sa.Column("submitted_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("task_id", "document_id"))
    for column in ("tenant_id", "project_id", "task_id", "document_id"):
        op.create_index(f"ix_project_task_documents_{column}", "project_task_documents", [column])
    op.create_table("project_task_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("customer_tenants.id"), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("implementation_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("project_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("document_ids", sa.JSON(), nullable=False),
        sa.Column("submission_comment", sa.Text(), nullable=False),
        sa.Column("decision_comment", sa.Text(), nullable=False),
        sa.Column("submitted_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("decided_by", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("task_id", "round"),
        sa.CheckConstraint("status IN ('pending','approved','rejected')", name="ck_project_task_review_status"))
    for column in ("tenant_id", "project_id", "task_id"):
        op.create_index(f"ix_project_task_reviews_{column}", "project_task_reviews", [column])
    if op.get_bind().dialect.name == "postgresql":
        for table in ("project_task_documents", "project_task_reviews"):
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            op.execute(f"CREATE POLICY {table}_access ON {table} USING (EXISTS (SELECT 1 FROM implementation_projects p WHERE p.id = {table}.project_id))")


def downgrade():
    op.execute("UPDATE project_tasks SET status = 'in_progress' WHERE status IN ('pending_review','changes_requested')")
    op.drop_table("project_task_reviews")
    op.drop_table("project_task_documents")
    with op.batch_alter_table("project_tasks") as batch:
        batch.drop_column("blocking_reason")
        batch.drop_constraint("ck_project_task_status", type_="check")
        batch.create_check_constraint("ck_project_task_status", "status IN ('todo','in_progress','blocked','done')")
