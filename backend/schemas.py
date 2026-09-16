from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.agent_types import AgentState


class Role(StrEnum):
    PLATFORM_ADMIN = "platform_admin"
    TENANT_ADMIN = "tenant_admin"
    CONSULTANT = "implementation_consultant"
    CUSTOMER = "customer_contact"
    APPROVER = "approver"
    VIEWER = "viewer"
    TENANT_MEMBER = "tenant_member"


class CompanyRole(StrEnum):
    TENANT_ADMIN = "tenant_admin"
    TENANT_MEMBER = "tenant_member"


class PlatformRole(StrEnum):
    SUPER_ADMIN = "platform_super_admin"
    OPERATOR = "platform_operator"
    SUPPORT = "platform_support"
    AUDITOR = "platform_auditor"


class ProjectRole(StrEnum):
    PROJECT_MANAGER = "project_manager"
    CONSULTANT = "implementation_consultant"
    APPROVER = "approver"
    CUSTOMER = "customer_contact"
    VIEWER = "viewer"


class RunStatus(StrEnum):
    BLOCKED = "blocked"
    PREPARING_MATERIALS = "preparing_materials"
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RequirementSpec(BaseModel):
    category: str
    statement: str = Field(min_length=3)
    priority: str = "medium"
    source: str = "customer_document"


class GapAnalysisItem(BaseModel):
    requirement: str
    capability: str | None = None
    fit: str = Field(pattern="^(supported|partial|gap|human_review)$")
    evidence_ids: list[str] = Field(default_factory=list)
    recommendation: str


class MilestoneSpec(BaseModel):
    name: str
    days: int = Field(ge=1, le=180)
    owner_role: str
    dependencies: list[str] = Field(default_factory=list)


class ImplementationPlan(BaseModel):
    milestones: list[MilestoneSpec] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_dependencies(self):
        names = {m.name for m in self.milestones}
        if len(names) != len(self.milestones):
            raise ValueError("里程碑名称必须唯一")
        remaining = {m.name: set(m.dependencies) for m in self.milestones}
        if any(not deps <= names for deps in remaining.values()):
            raise ValueError("里程碑依赖不存在")
        completed: set[str] = set()
        while remaining:
            ready = {name for name, deps in remaining.items() if deps <= completed}
            if not ready:
                raise ValueError("里程碑依赖存在环路")
            completed.update(ready)
            remaining = {name: deps for name, deps in remaining.items() if name not in ready}
        return self


class ConfigurationChange(BaseModel):
    path: str
    old_value: Any = None
    new_value: Any
    risk: str = Field(pattern="^(low|medium|high)$")
    reason: str


class FieldMapping(BaseModel):
    source: str
    target: str
    required: bool = False


class ImportErrorItem(BaseModel):
    row: int
    field: str
    message: str
    suggestion: str


class ImportValidationResult(BaseModel):
    valid: bool
    row_count: int
    mappings: list[FieldMapping]
    errors: list[ImportErrorItem] = Field(default_factory=list)


class ApprovalKind(StrEnum):
    PLAN = "plan"
    CONFIGURATION = "configuration"
    IMPORT = "import"
    ACCEPTANCE = "acceptance"
    EVIDENCE = "evidence"


class ApprovalDecision(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    comment: str = ""
    expected_version: int = Field(ge=1)


class GoLiveCheckResult(BaseModel):
    name: str
    passed: bool
    details: str


class AcceptanceReport(BaseModel):
    ready: bool
    checks: list[GoLiveCheckResult]
    blockers: list[str] = Field(default_factory=list)


class ImplementationGraphState(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    project_id: UUID
    run_id: UUID
    engine_version: str = "legacy"
    agent: AgentState = Field(default_factory=AgentState)
    current_node: str = "create_project"
    status: RunStatus = RunStatus.RUNNING
    requirements: list[RequirementSpec] = Field(default_factory=list)
    gap_items: list[GapAnalysisItem] = Field(default_factory=list)
    plan: ImplementationPlan | None = None
    configuration_changes: list[ConfigurationChange] = Field(default_factory=list)
    import_job_id: UUID | None = None
    acceptance_report: AcceptanceReport | None = None
    pending_approval_id: UUID | None = None
    completed_nodes: list[str] = Field(default_factory=list)
    blocking_reason: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    customer_name: str = Field(min_length=2, max_length=120)
    company_id: UUID | None = None
    assisting_company_id: UUID | None = None
    customer_contact: str = Field(min_length=2, max_length=80)
    contact_email: str = Field(
        min_length=5,
        max_length=160,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    employee_count: int = Field(ge=1, le=100_000)
    target_go_live_date: date
    departments: list[str] = Field(min_length=1, max_length=50)
    requirements_text: str = Field(min_length=20, max_length=10_000)
    industry: str = Field(default="", max_length=80)
    contact_phone: str = Field(default="", max_length=40)
    consultant_name: str = Field(default="", max_length=80)
    migration_scope: str = Field(default="", max_length=2_000)
    acceptance_criteria: str = Field(default="", max_length=2_000)
    notes: str = Field(default="", max_length=2_000)

    @model_validator(mode="after")
    def companies_must_be_different(self):
        if self.company_id and self.assisting_company_id == self.company_id:
            raise ValueError("协助公司不能与公司相同")
        return self

    @field_validator("departments")
    @classmethod
    def clean_string_list(cls, value: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if not cleaned:
            raise ValueError("At least one non-empty item is required")
        return cleaned

    @field_validator("requirements_text")
    @classmethod
    def clean_requirements_text(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 20:
            raise ValueError("具体实施需求去除空白后至少需要 20 个字符")
        return cleaned


class ProjectView(BaseModel):
    id: UUID
    name: str
    customer_name: str
    status: str
    created_at: datetime


class RunView(BaseModel):
    id: UUID
    project_id: UUID
    status: str
    current_node: str
    state: dict[str, Any]
    trace_id: str


class ResumeRequest(BaseModel):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=500)


class Envelope(BaseModel):
    data: Any
    request_id: str
    trace_id: str


class ImportValidateRequest(BaseModel):
    project_id: UUID
    run_id: UUID
    csv_text: str = Field(min_length=1)

    @field_validator("csv_text")
    @classmethod
    def size_limit(cls, value: str) -> str:
        if len(value.encode()) > 2_000_000:
            raise ValueError("CSV must be smaller than 2 MB")
        return value


class LoginRequest(BaseModel):
    email: str | None = Field(default=None, min_length=3, max_length=160)
    username: str | None = Field(default=None, min_length=3, max_length=160)
    password: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def identifier_required(self):
        if not self.email and not self.username:
            raise ValueError("请填写账号")
        if self.email and self.username and self.email.strip().lower() != self.username.strip().lower():
            raise ValueError("账号字段不能冲突")
        return self


class InvitationAcceptRequest(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=40, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    display_name: str = Field(default="", max_length=100)
    password: str = Field(default="", max_length=128)


class PasswordForgotRequest(BaseModel):
    email: str = Field(min_length=5, max_length=160)


class PasswordResetRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=10, max_length=128)


class TenantCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    slug: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    admin_email: str = Field(min_length=5, max_length=160)
    admin_name: str = Field(default="租户管理员", min_length=2, max_length=100)


class TenantUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    status: str | None = Field(default=None, pattern="^(active|suspended)$")


class InvitationCreateRequest(BaseModel):
    email: str = Field(min_length=5, max_length=160)
    display_name: str = Field(default="", max_length=100)
    company_role: CompanyRole = CompanyRole.TENANT_MEMBER
    tenant_id: UUID | None = None


class PlatformInvitationCreateRequest(BaseModel):
    email: str = Field(min_length=5, max_length=160)
    display_name: str = Field(default="", max_length=100)
    role_code: PlatformRole


class PlatformUserUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=2, max_length=100)
    status: str | None = Field(default=None, pattern="^(active|disabled)$")


class CompanyTransferRequest(BaseModel):
    target_tenant_id: UUID
    company_role: CompanyRole = CompanyRole.TENANT_MEMBER
    reason: str = Field(min_length=5, max_length=1000)


class ProjectMemberCreateRequest(BaseModel):
    user_id: UUID
    primary_role_code: ProjectRole | None = None
    project_role: ProjectRole | None = None

    @model_validator(mode="after")
    def resolve_role(self):
        if self.primary_role_code is None and self.project_role is None:
            raise ValueError("必须指定 primary_role_code")
        if self.primary_role_code and self.project_role and self.primary_role_code != self.project_role:
            raise ValueError("primary_role_code 与 project_role 不能冲突")
        self.primary_role_code = self.primary_role_code or self.project_role
        self.project_role = self.primary_role_code
        return self


class ProjectMemberUpdateRequest(BaseModel):
    primary_role_code: ProjectRole | None = None
    project_role: ProjectRole | None = None
    status: str | None = Field(default=None, pattern="^(active|disabled)$")

    @model_validator(mode="after")
    def resolve_role(self):
        if self.primary_role_code and self.project_role and self.primary_role_code != self.project_role:
            raise ValueError("primary_role_code 与 project_role 不能冲突")
        self.primary_role_code = self.primary_role_code or self.project_role
        self.project_role = self.primary_role_code
        return self


class ProjectCollaborationCreateRequest(BaseModel):
    tenant_id: UUID


class MembershipUpdateRequest(BaseModel):
    role: CompanyRole | None = None
    status: str | None = Field(default=None, pattern="^(active|disabled)$")


class CapabilityGrantCreateRequest(BaseModel):
    permission_code: str = Field(min_length=3, max_length=100)
    reason: str = Field(min_length=5, max_length=1000)
    expires_at: datetime


class SupportAccessCreateRequest(BaseModel):
    target_tenant_id: UUID
    target_project_id: UUID
    reason: str = Field(min_length=10, max_length=2000)
    permission_codes: list[str] = Field(min_length=1, max_length=10)
    expires_at: datetime | None = None


class SupportAccessDecisionRequest(BaseModel):
    comment: str = Field(default="", max_length=1000)
