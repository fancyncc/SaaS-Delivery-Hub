"""Track actual failed acceptance checks through remediation runs."""
import sqlalchemy as sa

from alembic import op

revision = "0013_remediation_tasks"
down_revision = "0012_knowledge_mail"
branch_labels = None
depends_on = None


def upgrade():
    if "remediation_tasks" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table("remediation_tasks", sa.Column("id", sa.String(36), primary_key=True), sa.Column("tenant_id", sa.String(36), sa.ForeignKey("customer_tenants.id"), nullable=False), sa.Column("project_id", sa.String(36), sa.ForeignKey("implementation_projects.id"), nullable=False), sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id"), nullable=False), sa.Column("check_name", sa.String(160), nullable=False), sa.Column("status", sa.String(20), nullable=False), sa.Column("evidence", sa.JSON(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("project_id", "check_name"))
        for column in ("tenant_id", "project_id", "run_id"):
            op.create_index(f"ix_remediation_tasks_{column}", "remediation_tasks", [column])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE remediation_tasks ENABLE ROW LEVEL SECURITY")
        op.execute("CREATE POLICY remediation_scope ON remediation_tasks USING (EXISTS (SELECT 1 FROM implementation_projects p WHERE p.id=remediation_tasks.project_id)) WITH CHECK (EXISTS (SELECT 1 FROM implementation_projects p WHERE p.id=remediation_tasks.project_id))")


def downgrade():
    op.drop_table("remediation_tasks")
