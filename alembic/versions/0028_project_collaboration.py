"""Project document submissions and collaborative tasks."""
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision = "0028_project_collaboration"
down_revision = "0027_scoped_memory"
branch_labels = None
depends_on = None

ROLES = ("company_admin", "project_manager", "implementation_consultant", "approver", "customer_contact")
PERMISSIONS = ("project.document.submit", "project.task.write")


def upgrade():
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("knowledge_documents")}
    if "submitted_by" not in existing:
        with op.batch_alter_table("knowledge_documents") as batch:
            batch.add_column(sa.Column("submitted_by", sa.String(36), nullable=True))
            batch.create_foreign_key("fk_knowledge_submitter", "users", ["submitted_by"], ["id"])
    op.create_table("project_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("implementation_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("customer_tenants.id"), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("assignee_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("due_date", sa.String(10)),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('todo','in_progress','blocked','done')", name="ck_project_task_status"))
    op.create_index("ix_project_tasks_project_id", "project_tasks", ["project_id"])
    op.create_index("ix_project_tasks_tenant_id", "project_tasks", ["tenant_id"])
    bind = op.get_bind()
    for code in PERMISSIONS:
        # psycopg reuses named parameters. Explicit types avoid conflicting
        # inference between INSERT targets, SELECT and the existence predicate.
        bind.execute(sa.text("INSERT INTO permission_definitions (id, code, resource, action, description, risk_level) SELECT :id, :code, 'project', :action, :description, 'low' WHERE NOT EXISTS (SELECT 1 FROM permission_definitions WHERE code = :code)").bindparams(
            sa.bindparam("id", type_=sa.String(36)),
            sa.bindparam("code", type_=sa.String(100)),
            sa.bindparam("action", type_=sa.String(60)),
            sa.bindparam("description", type_=sa.Text()),
        ),
                     {"id": str(uuid4()), "code": code, "action": code.split(".", 1)[1], "description": code})
        for role in ROLES:
            bind.execute(sa.text("INSERT INTO role_permissions (role_code, permission_code) SELECT :role, :code WHERE NOT EXISTS (SELECT 1 FROM role_permissions WHERE role_code = :role AND permission_code = :code)").bindparams(
                sa.bindparam("role", type_=sa.String(80)), sa.bindparam("code", type_=sa.String(100)),
            ), {"role": role, "code": code})
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE project_tasks ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE project_tasks FORCE ROW LEVEL SECURITY")
        op.execute("CREATE POLICY project_tasks_access ON project_tasks USING (EXISTS (SELECT 1 FROM implementation_projects p WHERE p.id = project_tasks.project_id))")
        # Keep the original tenant write policy; only extend SELECT for shared project materials.
        op.execute("CREATE POLICY knowledge_project_read ON knowledge_documents FOR SELECT USING (project_id IS NOT NULL AND EXISTS (SELECT 1 FROM implementation_projects p WHERE p.id = knowledge_documents.project_id AND p.deleted_at IS NULL))")


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS knowledge_project_read ON knowledge_documents")
    for code in PERMISSIONS:
        op.get_bind().execute(sa.text("DELETE FROM role_permissions WHERE permission_code = :code"), {"code": code})
        op.get_bind().execute(sa.text("DELETE FROM permission_definitions WHERE code = :code"), {"code": code})
    op.drop_table("project_tasks")
    foreign_keys = {key["name"] for key in sa.inspect(op.get_bind()).get_foreign_keys("knowledge_documents")}
    if "fk_knowledge_submitter" in foreign_keys:
        with op.batch_alter_table("knowledge_documents") as batch:
            batch.drop_constraint("fk_knowledge_submitter", type_="foreignkey")
            batch.drop_column("submitted_by")
