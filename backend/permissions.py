from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import exists, false, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    PermissionDefinition,
    Project,
    ProjectCapabilityGrant,
    ProjectCollaboration,
    ProjectMembership,
    RoleDefinition,
    RolePermission,
    SupportAccessGrant,
    SupportAccessGrantPermission,
    Tenant,
)
from backend.security import Principal

PROJECT_ROLES = {
    "project_manager": "项目经理",
    "implementation_consultant": "实施顾问",
    "approver": "审批人",
    "customer_contact": "客户联系人",
    "viewer": "只读成员",
}
COMPANY_ROLES = {"company_admin": "公司管理员", "company_member": "公司成员"}
PLATFORM_ROLES = {
    "platform_super_admin": "平台超级管理员",
    "platform_operator": "平台运营人员",
    "platform_support": "平台支持人员",
    "platform_auditor": "平台审计人员",
}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "platform_super_admin": {
        "company.create", "company.view", "company.update", "audit.view", "audit.export",
        "system.view", "support.request", "support.revoke", "platform.staff.manage",
        "user.view", "user.update", "user.transfer", "session.revoke", "customer.inspect",
        "evaluation.run",
    },
    "platform_operator": {
        "company.create", "company.view", "company.update", "audit.view",
        "user.view", "user.update", "user.transfer", "session.revoke",
    },
    "platform_support": {
        "company.view", "user.view", "system.view", "support.request", "support.revoke",
    },
    "platform_auditor": {
        "company.view", "user.view", "audit.view", "audit.export", "system.view",
    },
    "company_admin": {
        "company.view", "company.update", "member.view", "member.invite", "member.role.assign",
        "member.disable", "project.view", "project.create", "project.delete", "project.restore",
        "project.member.view", "project.member.assign", "project.member.revoke",
        "collaboration.view", "collaboration.invite", "collaboration.accept",
        "collaboration.reject", "collaboration.revoke", "run.view", "approval.view", "import.view",
        "acceptance.view", "artifact.view", "audit.view", "support.approve", "support.revoke",
    },
    "company_member": set(),
    "project_manager": {
        "project.view", "project.edit", "project.member.view", "project.member.assign",
        "project.member.revoke", "collaboration.view", "run.view", "run.start", "run.cancel",
        "run.retry", "approval.view", "import.view", "acceptance.view", "artifact.view", "audit.view",
    },
    "implementation_consultant": {
        "project.view", "project.edit", "project.member.view", "run.view", "run.start", "run.cancel",
        "run.retry", "approval.view", "import.view", "import.validate", "import.submit", "import.execute",
        "acceptance.view", "artifact.view", "artifact.create", "audit.view",
    },
    "approver": {
        "project.view", "run.view", "approval.view", "approval.decide", "import.view",
        "acceptance.view", "acceptance.decide", "artifact.view", "audit.view",
    },
    "customer_contact": {
        "project.view", "run.view", "approval.view", "acceptance.view", "acceptance.submit", "artifact.view",
    },
    "viewer": {"project.view", "run.view", "approval.view", "import.view", "acceptance.view", "artifact.view"},
}

for _role in ("company_admin", "project_manager", "implementation_consultant", "approver", "customer_contact"):
    ROLE_PERMISSIONS[_role].update({"project.document.submit", "project.task.write"})

PERMISSION_META: dict[str, tuple[str, str]] = {}
for role_permissions in ROLE_PERMISSIONS.values():
    for code in role_permissions:
        resource, action = code.split(".", 1)
        PERMISSION_META[code] = (resource, action)

GRANTABLE_CAPABILITIES = {
    "project.edit", "run.start", "run.cancel", "run.retry", "import.validate",
    "import.submit", "import.execute", "acceptance.submit",
}
SUPPORT_READ_PERMISSIONS = {
    "project.view", "run.view", "approval.view", "artifact.view", "audit.view",
}


async def ensure_permission_catalog(session: AsyncSession) -> None:
    existing_roles = set((await session.scalars(select(RoleDefinition.code))).all())
    for code, name in {**PLATFORM_ROLES, **COMPANY_ROLES, **PROJECT_ROLES}.items():
        if code not in existing_roles:
            scope = "platform" if code in PLATFORM_ROLES else "company" if code in COMPANY_ROLES else "project"
            session.add(RoleDefinition(code=code, scope=scope, name=name, description=name))
    existing_permissions = set((await session.scalars(select(PermissionDefinition.code))).all())
    for code, (resource, action) in PERMISSION_META.items():
        if code not in existing_permissions:
            session.add(PermissionDefinition(code=code, resource=resource, action=action, description=code))
    await session.flush()
    existing_pairs = set((await session.execute(select(RolePermission.role_code, RolePermission.permission_code))).all())
    for role_code, permissions in ROLE_PERMISSIONS.items():
        for permission_code in permissions:
            if (role_code, permission_code) not in existing_pairs:
                session.add(RolePermission(role_code=role_code, permission_code=permission_code))
    await session.flush()


def accessible_project_filter(user: Principal):
    company_scope = or_(
        Project.tenant_id == user.tenant_id,
        exists().where(
            ProjectCollaboration.project_id == Project.id,
            ProjectCollaboration.tenant_id == user.tenant_id,
            ProjectCollaboration.status == "active",
        ),
    )
    company_or_member = or_(
        true() if user.company_role_code == "company_admin" else false(),
        exists().where(
            ProjectMembership.project_id == Project.id,
            ProjectMembership.user_id == user.user_id,
            ProjectMembership.tenant_id == user.tenant_id,
            ProjectMembership.status == "active",
        ),
    )
    support = exists().where(
        SupportAccessGrant.target_project_id == Project.id,
        SupportAccessGrant.requester_user_id == user.user_id,
        SupportAccessGrant.status == "approved",
        SupportAccessGrant.revoked_at.is_(None),
        SupportAccessGrant.expires_at > datetime.now(UTC),
        exists().where(
            SupportAccessGrantPermission.grant_id == SupportAccessGrant.id,
            SupportAccessGrantPermission.permission_code == "project.view",
        ),
    )
    return (company_scope & company_or_member) | support


async def _role_permissions(session: AsyncSession, role_codes: set[str]) -> set[str]:
    if not role_codes:
        return set()
    return set((await session.scalars(
        select(RolePermission.permission_code).where(RolePermission.role_code.in_(role_codes))
    )).all())


async def project_access(session: AsyncSession, project: Project, user: Principal) -> dict:
    collaborator = await session.scalar(select(ProjectCollaboration).where(
        ProjectCollaboration.project_id == project.id,
        ProjectCollaboration.tenant_id == user.tenant_id,
        ProjectCollaboration.status == "active",
    ))
    in_company_scope = project.tenant_id == user.tenant_id or collaborator is not None
    role_codes: set[str] = set()
    source = ""
    membership = None
    if in_company_scope and user.company_role_code == "company_admin":
        role_codes.add("company_admin")
        source = "owner_company" if project.tenant_id == user.tenant_id else "collaborating_company"
    if in_company_scope:
        membership = await session.scalar(select(ProjectMembership).where(
            ProjectMembership.project_id == project.id,
            ProjectMembership.user_id == user.user_id,
            ProjectMembership.tenant_id == user.tenant_id,
            ProjectMembership.status == "active",
        ))
        if membership:
            role_codes.add(membership.primary_role_code)
            source = "project_membership"
    permissions = await _role_permissions(session, role_codes)
    owner_space = await session.get(Tenant, project.tenant_id)
    if (owner_space and owner_space.kind == "personal" and owner_space.personal_owner_id == user.user_id
            and project.tenant_id == user.tenant_id and user.session_context == "customer"):
        permissions.update(await _role_permissions(session, {"project_manager", "implementation_consultant", "approver"}))
    if membership:
        permissions.update((await session.scalars(select(ProjectCapabilityGrant.permission_code).where(
            ProjectCapabilityGrant.membership_id == membership.id,
            ProjectCapabilityGrant.revoked_at.is_(None),
            ProjectCapabilityGrant.expires_at > datetime.now(UTC),
        ))).all())
    support_permissions = set((await session.scalars(
        select(SupportAccessGrantPermission.permission_code)
        .join(SupportAccessGrant, SupportAccessGrant.id == SupportAccessGrantPermission.grant_id)
        .where(
            SupportAccessGrant.requester_user_id == user.user_id,
            SupportAccessGrant.target_project_id == project.id,
            SupportAccessGrant.status == "approved",
            SupportAccessGrant.revoked_at.is_(None),
            SupportAccessGrant.expires_at > datetime.now(UTC),
        )
    )).all())
    if support_permissions:
        permissions.update(support_permissions)
        source = "support_access"
    if not permissions or "project.view" not in permissions:
        raise HTTPException(404, "Project not found")
    if project.tenant_id != user.tenant_id:
        permissions.difference_update({"project.delete", "project.restore", "collaboration.invite", "collaboration.revoke"})
    return {
        "source": source,
        "role": membership.primary_role_code if membership else ("company_admin" if role_codes else "support_readonly"),
        "permissions": permissions,
    }


async def require_project_permission(
    session: AsyncSession, project: Project, user: Principal, permission: str
) -> dict:
    access = await project_access(session, project, user)
    if permission not in access["permissions"]:
        raise HTTPException(403, "项目权限不足")
    return access


async def accessible_project_or_404(
    session: AsyncSession, project_id: str, user: Principal, *, include_deleted: bool = False
) -> Project:
    query = select(Project).where(Project.id == project_id, accessible_project_filter(user))
    if not include_deleted:
        query = query.where(Project.deleted_at.is_(None))
    project = await session.scalar(query)
    if not project:
        raise HTTPException(404, "Project not found")
    return project
