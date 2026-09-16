from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uid() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "customer_tenants"
    __table_args__ = (
        UniqueConstraint("id", "kind", name="uq_tenant_id_kind"),
        CheckConstraint("kind IN ('personal','company')", name="ck_tenant_kind"),
        CheckConstraint("(kind = 'personal' AND personal_owner_id IS NOT NULL) OR (kind = 'company' AND personal_owner_id IS NULL)", name="ck_tenant_owner_kind"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String(20), default="company", server_default="company")
    personal_owner_id: Mapped[str | None] = mapped_column(String(36), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("account_type IN ('platform','customer')", name="ck_user_account_type"),
        Index("uq_users_email_normalized", text("lower(trim(email))"), unique=True),
        Index("uq_users_username_normalized", text("lower(trim(username))"), unique=True),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(40), unique=True, default=lambda: f"u_{uuid4().hex}")
    email: Mapped[str | None] = mapped_column(String(160), unique=True, index=True, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    account_type: Mapped[str] = mapped_column(String(20), default="customer", index=True)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TenantMembership(Base):
    __tablename__ = "tenant_memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id"),
        ForeignKeyConstraint(["tenant_id", "workspace_kind"], ["customer_tenants.id", "customer_tenants.kind"], name="fk_membership_workspace_kind"),
        Index(
            "uq_active_company_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'active' AND workspace_kind = 'company'"),
            sqlite_where=text("status = 'active' AND workspace_kind = 'company'"),
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_kind: Mapped[str] = mapped_column(String(20), default="company", server_default="company")
    role: Mapped[str] = mapped_column(String(40), index=True)
    company_role_code: Mapped[str] = mapped_column(ForeignKey("role_definitions.code"), default="company_member", index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserInvitation(Base):
    __tablename__ = "user_invitations"
    __table_args__ = (Index("uq_pending_company_invitation", "tenant_id", "email", unique=True,
                           postgresql_where=text("status = 'pending'"), sqlite_where=text("status = 'pending'")),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String(160), index=True)
    display_name: Mapped[str] = mapped_column(String(100), default="")
    role: Mapped[str] = mapped_column(String(40))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    invited_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EmailVerification(Base):
    __tablename__ = "email_verifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(160), index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CompanyDirectoryEntry(Base):
    """One employee identity shared by pending invitations and activated members."""
    __tablename__ = "company_directory_entries"
    __table_args__ = (UniqueConstraint("tenant_id", "email"), UniqueConstraint("tenant_id", "employee_number"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String(160))
    display_name: Mapped[str] = mapped_column(String(100))
    department: Mapped[str] = mapped_column(String(100))
    employee_number: Mapped[str | None] = mapped_column(String(80))


class CompanyMemberImport(Base):
    __tablename__ = "company_member_imports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    rows: Mapped[list] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="validated")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlatformInvitation(Base):
    __tablename__ = "platform_invitations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(160), index=True)
    display_name: Mapped[str] = mapped_column(String(100), default="")
    role_code: Mapped[str] = mapped_column(ForeignKey("role_definitions.code"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    invited_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        CheckConstraint("context_type IN ('platform','customer')", name="ck_auth_session_context"),
        CheckConstraint(
            "(context_type = 'platform' AND tenant_id IS NULL) OR "
            "(context_type = 'customer' AND tenant_id IS NOT NULL)",
            name="ck_auth_session_tenant_context",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("customer_tenants.id"), nullable=True, index=True)
    context_type: Mapped[str] = mapped_column(String(20), default="customer", index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    device_summary: Mapped[str] = mapped_column(String(255), default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Project(Base):
    __tablename__ = "implementation_projects"
    __table_args__ = (
        CheckConstraint(
            "lifecycle_status IN ('draft','ready','in_progress','blocked','completed','cancelled','archived')",
            name="ck_project_lifecycle_status",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    customer_name: Mapped[str] = mapped_column(String(120))
    requirements_text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    lifecycle_status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    deleted_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class ProjectDocument(Base):
    __tablename__ = "project_documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("implementation_projects.id"), unique=True)
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProjectMembership(Base):
    __tablename__ = "project_memberships"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("implementation_projects.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    project_role: Mapped[str] = mapped_column(String(40), index=True)
    primary_role_code: Mapped[str] = mapped_column(ForeignKey("role_definitions.code"), default="viewer", index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    granted_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RoleDefinition(Base):
    __tablename__ = "role_definitions"
    __table_args__ = (CheckConstraint("scope IN ('platform','company','project')", name="ck_role_scope"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    scope: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    built_in: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PermissionDefinition(Base):
    __tablename__ = "permission_definitions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    code: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    resource: Mapped[str] = mapped_column(String(60), index=True)
    action: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(Text, default="")
    risk_level: Mapped[str] = mapped_column(String(20), default="low")


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_code: Mapped[str] = mapped_column(ForeignKey("role_definitions.code"), primary_key=True)
    permission_code: Mapped[str] = mapped_column(ForeignKey("permission_definitions.code"), primary_key=True)


class PlatformRoleBinding(Base):
    __tablename__ = "platform_role_bindings"
    __table_args__ = (UniqueConstraint("user_id", "role_code"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    role_code: Mapped[str] = mapped_column(ForeignKey("role_definitions.code"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    granted_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProjectCapabilityGrant(Base):
    __tablename__ = "project_capability_grants"
    __table_args__ = (
        CheckConstraint("expires_at > granted_at", name="ck_capability_expiry"),
        Index(
            "uq_active_project_capability",
            "membership_id",
            "permission_code",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
            sqlite_where=text("revoked_at IS NULL"),
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    membership_id: Mapped[str] = mapped_column(ForeignKey("project_memberships.id"), index=True)
    permission_code: Mapped[str] = mapped_column(ForeignKey("permission_definitions.code"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    granted_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SupportAccessGrant(Base):
    __tablename__ = "support_access_grants"
    __table_args__ = (CheckConstraint("expires_at > requested_at", name="ck_support_expiry"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    requester_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    target_tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    target_project_id: Mapped[str] = mapped_column(ForeignKey("implementation_projects.id"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SupportAccessGrantPermission(Base):
    __tablename__ = "support_access_grant_permissions"
    grant_id: Mapped[str] = mapped_column(ForeignKey("support_access_grants.id"), primary_key=True)
    permission_code: Mapped[str] = mapped_column(ForeignKey("permission_definitions.code"), primary_key=True)


class ProjectCollaboration(Base):
    __tablename__ = "project_company_collaborations"
    __table_args__ = (UniqueConstraint("project_id", "tenant_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("implementation_projects.id"), index=True)
    owner_tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    invited_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    accepted_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("project_id", "run_number", name="uq_agent_run_project_number"),
        Index("uq_agent_run_active_project", "project_id", unique=True,
              postgresql_where=text("status IN ('pending','running','preparing_materials','waiting_approval','blocked')"),
              sqlite_where=text("status IN ('pending','running','preparing_materials','waiting_approval','blocked')")),
        CheckConstraint(
            "status IN ('pending','running','preparing_materials','waiting_approval','succeeded','failed','cancelled','blocked')",
            name="ck_agent_run_status",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("implementation_projects.id"), index=True)
    started_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    run_number: Mapped[int] = mapped_column(Integer)
    retry_of_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    current_node: Mapped[str] = mapped_column(String(80), default="create_project")
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    trace_id: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentStep(Base):
    __tablename__ = "agent_steps"
    __table_args__ = (UniqueConstraint("run_id", "sequence", name="uq_agent_step_run_sequence"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    node: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(32))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Approval(Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','rejected','expired','cancelled')",
            name="ck_approval_status",
        ),
        Index(
            "uq_pending_approval_per_run_kind",
            "run_id",
            "kind",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    version: Mapped[int] = mapped_column(Integer, default=1)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    requested_by: Mapped[str] = mapped_column(String(80), default="agent")
    decided_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("tenant_id", "scope", "key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    scope: Mapped[str] = mapped_column(String(80))
    key: Mapped[str] = mapped_column(String(160))
    response: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ImportJob(Base):
    __tablename__ = "import_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("implementation_projects.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id", name="fk_import_run"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="validated")
    source_hash: Mapped[str] = mapped_column(String(64))
    validation: Mapped[dict] = mapped_column(JSON)
    csv_text: Mapped[str] = mapped_column(Text)
    result: Mapped[dict] = mapped_column(JSON, default=dict)


class SaaSWorkspace(Base):
    __tablename__ = "saas_workspaces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("implementation_projects.id"), unique=True)
    configuration: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)


class SaaSMember(Base):
    __tablename__ = "saas_members"
    __table_args__ = (UniqueConstraint("workspace_id", "email"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("saas_workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(160))
    department: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(40))


class ToolExecution(Base):
    __tablename__ = "tool_executions"
    __table_args__ = (UniqueConstraint("project_id", "kind", "material_hash"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("implementation_projects.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    material_hash: Mapped[str] = mapped_column(String(64))
    before: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProjectArtifact(Base):
    __tablename__ = "project_artifacts"
    __table_args__ = (UniqueConstraint("run_id", "kind", "checksum"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("implementation_projects.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(160))
    version: Mapped[int] = mapped_column(Integer)
    checksum: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProjectFeedback(Base):
    __tablename__ = "project_feedback"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("implementation_projects.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    author: Mapped[str] = mapped_column(ForeignKey("users.id"))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkflowOutbox(Base):
    __tablename__ = "workflow_outbox"
    __table_args__ = (UniqueConstraint("run_id", "run_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    run_version: Mapped[int] = mapped_column(Integer)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    processed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (UniqueConstraint("tenant_id", "title", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("implementation_projects.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(160))
    version: Mapped[int] = mapped_column(Integer)
    module: Mapped[str] = mapped_column(String(60))
    source: Mapped[str] = mapped_column(String(500))
    license: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    embedding_model: Mapped[str] = mapped_column(String(120), default="")
    index_status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    index_attempts: Mapped[int] = mapped_column(Integer, default=0)
    index_error: Mapped[str] = mapped_column(String(100), default="")
    index_version: Mapped[str] = mapped_column(String(160), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MailDelivery(Base):
    __tablename__ = "mail_deliveries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(160))
    purpose: Mapped[str] = mapped_column(String(80))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RemediationTask(Base):
    __tablename__ = "remediation_tasks"
    __table_args__ = (UniqueConstraint("project_id", "check_name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("customer_tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("implementation_projects.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    check_name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20), default="open")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("customer_tenants.id"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    actor: Mapped[str] = mapped_column(String(160))
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    resource_id: Mapped[str] = mapped_column(String(36), index=True)
    request_id: Mapped[str] = mapped_column(String(64), default="")
    ip_hash: Mapped[str] = mapped_column(String(64), default="")
    outcome: Mapped[str] = mapped_column(String(32), default="success")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("customer_tenants.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="completed")
    dataset_size: Mapped[str] = mapped_column(String(16))
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
