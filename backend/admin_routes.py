from __future__ import annotations

import csv
import io
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit import audit_event
from backend.config import get_settings
from backend.db import get_session
from backend.mailer import send_account_link
from backend.models import (
    AgentRun,
    AuditEvent,
    CompanyDirectoryEntry,
    Project,
    ProjectCollaboration,
    Tenant,
    TenantMembership,
    User,
    UserInvitation,
)
from backend.rate_limit import enforce_rate_limit
from backend.schemas import (
    InvitationCreateRequest,
    MembershipUpdateRequest,
    Role,
    TenantCreateRequest,
    TenantUpdateRequest,
)
from backend.security import Principal, require_company_admin, token_hash

router = APIRouter(prefix="/api/company", tags=["company administration"])


def _tenant_scope(user: Principal, requested: str | None = None) -> str:
    if requested and requested != user.tenant_id:
        raise HTTPException(403, "不能管理其他租户")
    scope = requested or user.tenant_id
    if not scope:
        raise HTTPException(403, "公司上下文缺失")
    return scope


async def _tenant_or_404(session: AsyncSession, tenant_id: str) -> Tenant:
    tenant = await session.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(404, "租户不存在")
    return tenant


@router.get("/dashboard")
async def dashboard(
    user: Principal = Depends(require_company_admin), session: AsyncSession = Depends(get_session)
):
    tenant_filter = [] if user.is_platform_admin else [Project.tenant_id == user.tenant_id]
    tenant_count = await session.scalar(
        select(func.count()).select_from(Tenant).where(Tenant.deleted_at.is_(None))
    ) if user.is_platform_admin else 1
    user_query = select(func.count(func.distinct(TenantMembership.user_id))).where(
        TenantMembership.status == "active"
    )
    if not user.is_platform_admin:
        user_query = user_query.where(TenantMembership.tenant_id == user.tenant_id)
    projects = (
        await session.execute(
            select(Project.lifecycle_status, func.count(Project.id))
            .where(Project.deleted_at.is_(None), *tenant_filter)
            .group_by(Project.lifecycle_status)
        )
    ).all()
    runs_query = select(func.count()).select_from(AgentRun).where(AgentRun.status == "failed")
    if not user.is_platform_admin:
        runs_query = runs_query.where(AgentRun.tenant_id == user.tenant_id)
    return {
        "data": {
            "tenant_count": tenant_count or 0,
            "active_user_count": await session.scalar(user_query) or 0,
            "project_statuses": {status: count for status, count in projects},
            "failed_run_count": await session.scalar(runs_query) or 0,
        }
    }


@router.get("/tenants")
async def list_tenants(
    user: Principal = Depends(require_company_admin), session: AsyncSession = Depends(get_session)
):
    query = select(Tenant).order_by(Tenant.created_at.desc())
    if not user.is_platform_admin:
        query = query.where(Tenant.id == user.tenant_id)
    rows = (await session.scalars(query)).all()
    return {"data": [{"id": x.id, "name": x.name, "slug": x.slug, "status": x.status,
                      "deleted_at": x.deleted_at.isoformat() if x.deleted_at else None} for x in rows]}


@router.get("/company-directory")
async def company_directory(
    user: Principal = Depends(require_company_admin), session: AsyncSession = Depends(get_session)
):
    rows = (await session.scalars(
        select(Tenant).where(Tenant.kind == "company", Tenant.status == "active", Tenant.deleted_at.is_(None)).order_by(Tenant.name)
    )).all()
    return {"data": [{"id": item.id, "name": item.name} for item in rows]}


async def create_tenant(
    payload: TenantCreateRequest,
    request: Request,
    user: Principal = Depends(require_company_admin),
    session: AsyncSession = Depends(get_session),
):
    if not user.is_platform_admin:
        raise HTTPException(403, "只有平台管理员可以创建租户")
    if await session.scalar(select(Tenant).where(Tenant.slug == payload.slug)):
        raise HTTPException(409, "租户标识已存在")
    tenant = Tenant(name=payload.name.strip(), slug=payload.slug)
    session.add(tenant)
    await session.flush()
    raw = secrets.token_urlsafe(48)
    invitation = UserInvitation(
        tenant_id=tenant.id,
        email=payload.admin_email.strip().lower(),
        display_name=payload.admin_name.strip(),
        role=Role.TENANT_ADMIN.value,
        token_hash=token_hash(raw),
        invited_by=user.user_id,
        expires_at=datetime.now(UTC) + timedelta(hours=48),
    )
    session.add(invitation)
    session.add(audit_event(request, user, "tenant.created", tenant.id, {"name": tenant.name}))
    url = f"{get_settings().frontend_base_url}/accept-invitation?token={quote(raw)}"
    await send_account_link(invitation.email, "invitation", url, session=session)
    await session.commit()
    data = {"id": tenant.id, "name": tenant.name, "slug": tenant.slug,
            "invitation_id": invitation.id}
    if get_settings().mail_debug:
        data["invitation_url"] = url
    return {"data": data}


async def update_tenant(
    tenant_id: UUID,
    payload: TenantUpdateRequest,
    request: Request,
    user: Principal = Depends(require_company_admin),
    session: AsyncSession = Depends(get_session),
):
    if not user.is_platform_admin:
        raise HTTPException(403, "只有平台管理员可以修改租户状态")
    tenant = await _tenant_or_404(session, str(tenant_id))
    before = {"name": tenant.name, "status": tenant.status}
    if payload.name is not None:
        tenant.name = payload.name.strip()
    if payload.status is not None:
        tenant.status = payload.status
    tenant.updated_at = datetime.now(UTC)
    session.add(audit_event(request, user, "tenant.updated", tenant.id, {"before": before, "after": {"name": tenant.name, "status": tenant.status}}))
    await session.commit()
    return {"data": {"id": tenant.id, "name": tenant.name, "status": tenant.status}}


async def delete_tenant(
    tenant_id: UUID,
    request: Request,
    user: Principal = Depends(require_company_admin),
    session: AsyncSession = Depends(get_session),
):
    if not user.is_platform_admin:
        raise HTTPException(403, "只有平台管理员可以删除租户")
    tenant = await _tenant_or_404(session, str(tenant_id))
    if tenant.id == user.tenant_id:
        raise HTTPException(409, "不能删除当前会话所在租户")
    tenant.deleted_at = datetime.now(UTC)
    tenant.deleted_by = user.user_id
    tenant.status = "deleted"
    session.add(audit_event(request, user, "tenant.deleted", tenant.id, {"name": tenant.name}))
    await session.commit()
    return {"data": {"deleted": True, "id": tenant.id}}


async def restore_tenant(
    tenant_id: UUID,
    request: Request,
    user: Principal = Depends(require_company_admin),
    session: AsyncSession = Depends(get_session),
):
    if not user.is_platform_admin:
        raise HTTPException(403, "只有平台管理员可以恢复租户")
    tenant = await _tenant_or_404(session, str(tenant_id))
    if tenant.deleted_at is None:
        raise HTTPException(409, "租户未被删除")
    tenant.deleted_at = None
    tenant.deleted_by = None
    tenant.status = "active"
    session.add(audit_event(request, user, "tenant.restored", tenant.id, {"name": tenant.name}))
    await session.commit()
    return {"data": {"restored": True, "id": tenant.id}}


@router.get("/members")
async def list_members(
    tenant_id: UUID | None = None,
    user: Principal = Depends(require_company_admin),
    session: AsyncSession = Depends(get_session),
):
    scope = _tenant_scope(user, str(tenant_id) if tenant_id else None)
    rows = (
        await session.execute(
            select(TenantMembership, User)
            .join(User, User.id == TenantMembership.user_id)
            .where(TenantMembership.tenant_id == scope, User.deleted_at.is_(None))
            .order_by(User.display_name.asc())
        )
    ).all()
    invitation_rows = (
        await session.execute(
            select(UserInvitation, User)
            .outerjoin(User, User.id == UserInvitation.invited_by)
            .where(UserInvitation.tenant_id == scope)
            .order_by(UserInvitation.created_at.desc())
        )
    ).all()
    invitation_emails = {item.email for item, _ in invitation_rows}
    accepted_users = (
        await session.scalars(select(User).where(User.email.in_(invitation_emails)))
    ).all() if invitation_emails else []
    accepted_by_email = {member.email: member for member in accepted_users}
    directory = {entry.email: entry for entry in (await session.scalars(select(CompanyDirectoryEntry).where(
        CompanyDirectoryEntry.tenant_id == scope))).all()}
    now = datetime.now(UTC)

    def invitation_data(item: UserInvitation, inviter: User | None) -> dict:
        expires_at = item.expires_at
        comparable_expiry = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
        status = "expired" if item.status == "pending" and comparable_expiry <= now else item.status
        accepted_user = accepted_by_email.get(item.email) if item.status == "accepted" else None
        return {
            "id": item.id,
            "email": item.email,
            "display_name": item.display_name,
            "role": item.role,
            "status": status,
            "invited_by_user_id": item.invited_by,
            "invited_by_name": inviter.display_name if inviter else "已删除用户",
            "invited_by_email": inviter.email if inviter else None,
            "created_at": item.created_at.isoformat(),
            "expires_at": item.expires_at.isoformat(),
            "accepted_at": item.accepted_at.isoformat() if item.accepted_at else None,
            "accepted_user_id": accepted_user.id if accepted_user else None,
            "accepted_user_name": accepted_user.display_name if accepted_user else None,
            "department": directory[item.email].department if item.email in directory else "",
            "employee_number": directory[item.email].employee_number if item.email in directory else None,
        }
    return {"data": {"members": [
        {"membership_id": membership.id, "user_id": member.id, "email": member.email,
         "display_name": directory[member.email].display_name if member.email in directory else member.display_name,
         "department": directory[member.email].department if member.email in directory else "",
         "employee_number": directory[member.email].employee_number if member.email in directory else None,
         "role": membership.role,
         "company_role_code": membership.company_role_code, "status": membership.status}
        for membership, member in rows
    ], "invitations": [invitation_data(item, inviter) for item, inviter in invitation_rows]}}


@router.post("/invitations")
async def create_invitation(
    payload: InvitationCreateRequest,
    request: Request,
    user: Principal = Depends(require_company_admin),
    session: AsyncSession = Depends(get_session),
):
    if user.workspace_kind != "company":
        raise HTTPException(403, "个人空间不能添加公司成员")
    await enforce_rate_limit(f"member-invite:{user.tenant_id}", 30, 3600)
    await session.scalar(select(Tenant).where(Tenant.id == user.tenant_id).with_for_update())
    if user.is_platform_admin and payload.tenant_id and str(payload.tenant_id) != user.tenant_id:
        raise HTTPException(403, "平台管理员不能替其他公司邀请普通成员")
    scope = _tenant_scope(user, str(payload.tenant_id) if payload.tenant_id else None)
    email = payload.email.strip().lower()
    existing_user = await session.scalar(select(User).where(User.email == email))
    if existing_user:
        if existing_user.account_type != "customer" or existing_user.status != "active" or existing_user.deleted_at:
            raise HTTPException(409, "该账号不可加入")
        membership = await session.scalar(select(TenantMembership).where(
            TenantMembership.user_id == existing_user.id,
            TenantMembership.status == "active",
            TenantMembership.workspace_kind == "company",
        ))
        if membership:
            message = "该用户已是当前公司成员" if membership.tenant_id == scope else "一个账号只能属于一家公司"
            raise HTTPException(409, message)
    pending = await session.scalar(select(UserInvitation).where(
        UserInvitation.tenant_id == scope,
        UserInvitation.email == email,
        UserInvitation.status == "pending",
    ))
    if pending:
        pending.status = "revoked"
        await session.flush()
    raw = secrets.token_urlsafe(48)
    item = UserInvitation(
        tenant_id=scope,
        email=email,
        display_name=payload.display_name.strip(),
        role=payload.company_role.value,
        token_hash=token_hash(raw),
        invited_by=user.user_id,
        expires_at=datetime.now(UTC) + timedelta(hours=48),
    )
    session.add(item)
    await session.flush()
    session.add(audit_event(request, user, "invitation.created", item.id, {"email": email, "company_role": item.role, "tenant_id": scope}))
    url = f"{get_settings().frontend_base_url}/accept-invitation?token={quote(raw)}"
    await send_account_link(email, "invitation", url, session=session)
    await session.commit()
    data = {"id": item.id, "expires_at": item.expires_at.isoformat()}
    if get_settings().mail_debug:
        data["invitation_url"] = url
    return {"data": data}


async def _release_unused_directory_entry(session: AsyncSession, tenant_id: str, email: str) -> None:
    await session.flush()
    pending = await session.scalar(select(UserInvitation.id).where(UserInvitation.tenant_id == tenant_id,
        UserInvitation.email == email, UserInvitation.status == "pending"))
    member = await session.scalar(select(TenantMembership.id).join(User, User.id == TenantMembership.user_id).where(
        TenantMembership.tenant_id == tenant_id, User.email == email))
    if not pending and not member:
        await session.execute(delete(CompanyDirectoryEntry).where(
            CompanyDirectoryEntry.tenant_id == tenant_id, CompanyDirectoryEntry.email == email))


@router.post("/invitations/{invitation_id}/revoke")
async def revoke_invitation(
    invitation_id: UUID,
    request: Request,
    user: Principal = Depends(require_company_admin),
    session: AsyncSession = Depends(get_session),
):
    item = await session.get(UserInvitation, str(invitation_id))
    if not item or item.tenant_id != _tenant_scope(user, item.tenant_id):
        raise HTTPException(404, "邀请不存在")
    if item.status != "pending":
        raise HTTPException(409, "邀请已失效")
    item.status = "revoked"
    await _release_unused_directory_entry(session, item.tenant_id, item.email)
    session.add(audit_event(request, user, "invitation.revoked", item.id, {"email": item.email}))
    await session.commit()
    return {"data": {"revoked": True}}


@router.post("/invitations/{invitation_id}/resend")
async def resend_invitation(
    invitation_id: UUID,
    request: Request,
    user: Principal = Depends(require_company_admin),
    session: AsyncSession = Depends(get_session),
):
    await enforce_rate_limit(f"member-resend:{user.tenant_id}", 30, 3600)
    previous = await session.get(UserInvitation, str(invitation_id))
    if not previous or previous.tenant_id != _tenant_scope(user, previous.tenant_id):
        raise HTTPException(404, "邀请不存在")
    if previous.status == "accepted":
        raise HTTPException(409, "已接受的邀请不能重发")
    previous.status = "revoked"
    await session.flush()
    raw = secrets.token_urlsafe(48)
    item = UserInvitation(tenant_id=previous.tenant_id, email=previous.email,
                          display_name=previous.display_name, role=previous.role,
                          token_hash=token_hash(raw), invited_by=user.user_id,
                          expires_at=datetime.now(UTC) + timedelta(hours=48))
    session.add(item)
    await session.flush()
    session.add(audit_event(request, user, "invitation.resent", item.id, {"email": item.email}))
    url = f"{get_settings().frontend_base_url}/accept-invitation?token={quote(raw)}"
    await send_account_link(item.email, "invitation", url, session=session)
    await session.commit()
    data = {"id": item.id, "expires_at": item.expires_at.isoformat()}
    if get_settings().mail_debug:
        data["invitation_url"] = url
    return {"data": data}


@router.delete("/invitations/{invitation_id}")
async def delete_invitation(
    invitation_id: UUID,
    request: Request,
    user: Principal = Depends(require_company_admin),
    session: AsyncSession = Depends(get_session),
):
    item = await session.get(UserInvitation, str(invitation_id))
    if not item or item.tenant_id != _tenant_scope(user, item.tenant_id):
        raise HTTPException(404, "邀请不存在")
    snapshot = {
        "email": item.email,
        "display_name": item.display_name,
        "role": item.role,
        "status": item.status,
        "created_at": item.created_at.isoformat(),
        "accepted_at": item.accepted_at.isoformat() if item.accepted_at else None,
    }
    session.add(audit_event(request, user, "invitation.deleted", item.id, snapshot))
    await session.delete(item)
    await _release_unused_directory_entry(session, item.tenant_id, item.email)
    await session.commit()
    return {"data": {"deleted": True, "id": str(invitation_id)}}


@router.patch("/members/{membership_id}")
async def update_member(
    membership_id: UUID,
    payload: MembershipUpdateRequest,
    request: Request,
    user: Principal = Depends(require_company_admin),
    session: AsyncSession = Depends(get_session),
):
    if user.workspace_kind != "company":
        raise HTTPException(403, "个人空间身份不能修改")
    membership = await session.get(TenantMembership, str(membership_id))
    if not membership or membership.tenant_id != _tenant_scope(user, membership.tenant_id):
        raise HTTPException(404, "成员不存在")
    if membership.user_id == user.user_id and payload.status == "disabled":
        raise HTTPException(409, "不能停用自己的当前成员关系")
    before = {"role": membership.role, "status": membership.status}
    if payload.role is not None:
        membership.role = payload.role.value
        membership.company_role_code = (
            "company_admin" if payload.role.value == "tenant_admin" else "company_member"
        )
    if payload.status is not None:
        membership.status = payload.status
    session.add(audit_event(request, user, "membership.updated", membership.id, {"before": before, "after": {"role": membership.role, "status": membership.status}}))
    await session.commit()
    return {"data": {"id": membership.id, "role": membership.role,
                       "company_role_code": membership.company_role_code, "status": membership.status}}


@router.post("/members/{membership_id}/password-reset")
async def member_password_reset(
    membership_id: UUID,
    request: Request,
    user: Principal = Depends(require_company_admin),
    session: AsyncSession = Depends(get_session),
):
    membership = await session.get(TenantMembership, str(membership_id))
    if not membership or membership.tenant_id != _tenant_scope(user, membership.tenant_id):
        raise HTTPException(404, "成员不存在")
    member = await session.get(User, membership.user_id)
    if not member:
        raise HTTPException(404, "用户不存在")
    if not member.email:
        raise HTTPException(409, "用户尚未绑定邮箱")
    from backend.models import PasswordResetToken
    raw = secrets.token_urlsafe(48)
    session.add(PasswordResetToken(user_id=member.id, token_hash=token_hash(raw),
                                   expires_at=datetime.now(UTC) + timedelta(hours=1)))
    session.add(audit_event(request, user, "member.password_reset_requested", member.id, {"email": member.email}))
    url = f"{get_settings().frontend_base_url}/reset-password?token={quote(raw)}"
    await send_account_link(member.email, "password-reset", url, session=session)
    await session.commit()
    return {"data": {"sent": True, **({"preview_url": url} if get_settings().mail_debug else {})}}


@router.get("/projects")
async def admin_projects(
    tenant_id: UUID | None = None,
    status: str | None = None,
    include_deleted: bool = False,
    user: Principal = Depends(require_company_admin),
    session: AsyncSession = Depends(get_session),
):
    scope = _tenant_scope(user, str(tenant_id) if tenant_id else None)
    collaboration_exists = select(ProjectCollaboration.id).where(
        ProjectCollaboration.project_id == Project.id,
        ProjectCollaboration.tenant_id == scope,
        ProjectCollaboration.status == "active",
    ).exists()
    query = select(Project).where((Project.tenant_id == scope) | collaboration_exists)
    query = query.where(Project.deleted_at.is_not(None) if include_deleted else Project.deleted_at.is_(None))
    if status:
        query = query.where(Project.lifecycle_status == status)
    rows = (await session.scalars(query.order_by(Project.created_at.desc()))).all()
    return {"data": [{"id": x.id, "tenant_id": x.tenant_id, "name": x.name, "customer_name": x.customer_name,
                      "status": x.lifecycle_status, "lifecycle_status": x.lifecycle_status,
                      "version": x.version, "owner_user_id": x.owner_user_id,
                      "deleted_at": x.deleted_at.isoformat() if x.deleted_at else None} for x in rows]}


@router.get("/projects/trash")
async def project_trash(
    tenant_id: UUID | None = None,
    user: Principal = Depends(require_company_admin),
    session: AsyncSession = Depends(get_session),
):
    return await admin_projects(tenant_id, None, True, user, session)


@router.post("/projects/{project_id}/restore")
async def restore_project(
    project_id: UUID,
    request: Request,
    user: Principal = Depends(require_company_admin),
    session: AsyncSession = Depends(get_session),
):
    project = await session.scalar(select(Project).where(
        Project.id == str(project_id), Project.tenant_id == user.tenant_id
    ))
    if not project or project.deleted_at is None:
        raise HTTPException(404, "回收站中不存在该项目")
    deleted_at = project.deleted_at.replace(tzinfo=UTC) if project.deleted_at.tzinfo is None else project.deleted_at
    if deleted_at < datetime.now(UTC) - timedelta(days=30):
        raise HTTPException(410, "项目已超过可恢复期限")
    project.deleted_at = None
    project.deleted_by = None
    session.add(audit_event(request, user, "project.restored", project.id, {"name": project.name}))
    await session.commit()
    return {"data": {"restored": True, "id": project.id}}


@router.delete("/projects/{project_id}/purge")
async def purge_project(
    project_id: UUID,
    request: Request,
    user: Principal = Depends(require_company_admin),
    session: AsyncSession = Depends(get_session),
):
    project = await session.get(Project, str(project_id))
    if not project or project.tenant_id != user.tenant_id:
        raise HTTPException(404, "项目不存在")
    if project.deleted_at is None:
        raise HTTPException(409, "只能物理清理回收站中的项目")
    deleted_at = project.deleted_at.replace(tzinfo=UTC) if project.deleted_at.tzinfo is None else project.deleted_at
    if deleted_at > datetime.now(UTC) - timedelta(days=30):
        raise HTTPException(409, "项目需保留满 30 天后才能物理清理")
    from backend.cleanup import purge_project_data
    session.add(audit_event(request, user, "project.purged", project.id, {"name": project.name}))
    await purge_project_data(session, project)
    await session.commit()
    return {"data": {"purged": True, "id": str(project_id)}}


@router.get("/audit-events")
async def list_audit_events(
    tenant_id: UUID | None = None,
    event_type: str | None = None,
    limit: int = 100,
    user: Principal = Depends(require_company_admin),
    session: AsyncSession = Depends(get_session),
):
    scope = _tenant_scope(user, str(tenant_id) if tenant_id else None)
    query = select(AuditEvent).where(AuditEvent.tenant_id == scope)
    if event_type:
        query = query.where(AuditEvent.event_type == event_type)
    rows = (await session.scalars(query.order_by(AuditEvent.created_at.desc()).limit(min(limit, 500)))).all()
    return {"data": [{"id": x.id, "event_type": x.event_type, "actor": x.actor,
                      "resource_id": x.resource_id, "outcome": x.outcome, "payload": x.payload,
                      "created_at": x.created_at.isoformat()} for x in rows]}


@router.get("/audit-events/export.csv")
async def export_audit_events(
    tenant_id: UUID | None = None,
    user: Principal = Depends(require_company_admin),
    session: AsyncSession = Depends(get_session),
):
    scope = _tenant_scope(user, str(tenant_id) if tenant_id else None)
    rows = (await session.scalars(select(AuditEvent).where(AuditEvent.tenant_id == scope).order_by(AuditEvent.created_at.desc()))).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "event_type", "actor", "resource_id", "outcome", "created_at"])
    for item in rows:
        writer.writerow([item.id, item.event_type, item.actor, item.resource_id, item.outcome, item.created_at.isoformat()])
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=audit-events.csv"})


@router.get("/exports/tenant")
async def export_tenant_data(
    request: Request,
    tenant_id: UUID | None = None,
    user: Principal = Depends(require_company_admin),
    session: AsyncSession = Depends(get_session),
):
    scope = _tenant_scope(user, str(tenant_id) if tenant_id else None)
    projects = (await session.scalars(select(Project).where(Project.tenant_id == scope))).all()
    members = (await session.execute(select(TenantMembership, User).join(User, User.id == TenantMembership.user_id).where(TenantMembership.tenant_id == scope))).all()
    if request is not None:
        session.add(audit_event(request, user, "tenant.data_exported", scope, {"project_count": len(projects), "member_count": len(members)}))
        await session.commit()
    return {"data": {"tenant_id": scope, "exported_at": datetime.now(UTC).isoformat(),
                     "projects": [{"id": x.id, "name": x.name,
                                   "status": x.lifecycle_status,
                                   "requirements_text": x.requirements_text} for x in projects],
                     "members": [{"email": member.email, "display_name": member.display_name,
                                  "role": membership.role,
                                  "company_role_code": membership.company_role_code,
                                  "status": membership.status} for membership, member in members]}}


async def system_status(
    user: Principal = Depends(require_company_admin), session: AsyncSession = Depends(get_session)
):
    if not user.is_platform_admin:
        raise HTTPException(403, "只有平台管理员可以查看系统状态")
    await session.scalar(select(func.count()).select_from(Tenant))
    try:
        migration = (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar_one_or_none()
    except Exception:
        migration = "not_initialized"
        await session.rollback()
    return {"data": {"database": "connected", "migration": migration, "queue": "redis", "api": "healthy", "sql_console": False}}
