"""Self-service identities, personal spaces and employee registration batches."""
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision = "0014_open_registration"
down_revision = "0013_remediation_tasks"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    # Run every conflict check before changing schema or normalizing data.
    users = list(bind.execute(sa.text("SELECT id, email, display_name, account_type FROM users")).mappings())
    seen = {}
    conflicts = []
    for user in users:
        email = user["email"].strip().lower()
        if email in seen:
            conflicts.append(f"users:{seen[email]},{user['id']}")
        seen[email] = user["id"]
    pending = list(bind.execute(sa.text("SELECT id, tenant_id, email FROM user_invitations WHERE status='pending'")).mappings())
    invitations = {}
    for item in pending:
        key = (item["tenant_id"], item["email"].strip().lower())
        if key in invitations:
            conflicts.append(f"invitations:{invitations[key]},{item['id']}")
        invitations[key] = item["id"]
    if conflicts:
        raise RuntimeError("Normalization conflicts; resolve without merging accounts: " + "; ".join(conflicts))
    def has_column(table, column):
        return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}

    if not has_column("customer_tenants", "kind"):
        with op.batch_alter_table("customer_tenants") as batch:
            batch.add_column(sa.Column("kind", sa.String(20), nullable=False, server_default="company"))
            batch.add_column(sa.Column("personal_owner_id", sa.String(36), nullable=True))
            batch.create_unique_constraint("uq_tenant_personal_owner", ["personal_owner_id"])
            batch.create_unique_constraint("uq_tenant_id_kind", ["id", "kind"])
            batch.create_check_constraint("ck_tenant_kind", "kind IN ('personal','company')")
            batch.create_check_constraint("ck_tenant_owner_kind", "(kind='personal' AND personal_owner_id IS NOT NULL) OR (kind='company' AND personal_owner_id IS NULL)")
    if not has_column("users", "email_verified_at"):
        op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
    indexes = {i["name"] for i in sa.inspect(bind).get_indexes("tenant_memberships")}
    if "uq_active_company_per_user" in indexes:
        op.drop_index("uq_active_company_per_user", table_name="tenant_memberships")
    if not has_column("tenant_memberships", "workspace_kind"):
        with op.batch_alter_table("tenant_memberships") as batch:
            batch.add_column(sa.Column("workspace_kind", sa.String(20), nullable=False, server_default="company"))
            batch.create_foreign_key("fk_membership_workspace_kind", "customer_tenants", ["tenant_id", "workspace_kind"], ["id", "kind"])
    op.create_index("uq_active_company_per_user", "tenant_memberships", ["user_id"], unique=True,
                    postgresql_where=sa.text("status='active' AND workspace_kind='company'"),
                    sqlite_where=sa.text("status='active' AND workspace_kind='company'"))
    for table in ("users", "user_invitations", "platform_invitations"):
        bind.execute(sa.text(f"UPDATE {table} SET email=lower(trim(email))"))
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email_normalized ON users (lower(trim(email)))")
    if "uq_pending_company_invitation" not in {i["name"] for i in sa.inspect(bind).get_indexes("user_invitations")}:
        op.create_index("uq_pending_company_invitation", "user_invitations", ["tenant_id", "email"], unique=True,
                        postgresql_where=sa.text("status='pending'"), sqlite_where=sa.text("status='pending'"))
    # These new tables carry no legacy employee numbers; uniqueness applies from creation.
    from backend.models import CompanyDirectoryEntry, CompanyMemberImport, EmailVerification
    for model in (EmailVerification, CompanyDirectoryEntry, CompanyMemberImport):
        model.__table__.create(bind, checkfirst=True)
    if bind.dialect.name == "postgresql":
        for table in ("company_directory_entries", "company_member_imports"):
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"CREATE POLICY {table}_scope ON {table} USING (tenant_id = current_setting('app.current_tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))")
    tenant_table = sa.Table("customer_tenants", sa.MetaData(), autoload_with=bind)
    membership_table = sa.Table("tenant_memberships", sa.MetaData(), autoload_with=bind)
    now = datetime.now(UTC)
    for user in users:
        if user["account_type"] != "customer":
            continue
        if bind.execute(sa.select(tenant_table.c.id).where(tenant_table.c.personal_owner_id == user["id"])).first():
            continue
        space_id = str(uuid4())
        bind.execute(tenant_table.insert().values(id=space_id, kind="personal", personal_owner_id=user["id"],
            name=f"{user['display_name']}的个人空间", slug=f"personal-{user['id']}", status="active", settings={},
            created_at=now, updated_at=now))
        bind.execute(membership_table.insert().values(id=str(uuid4()), tenant_id=space_id, user_id=user["id"],
            workspace_kind="personal", role="tenant_admin", company_role_code="company_admin", status="active",
            created_at=now, updated_at=now))


def downgrade():
    raise RuntimeError("Personal spaces can contain user data. Restore a pre-migration backup instead of a destructive downgrade.")
