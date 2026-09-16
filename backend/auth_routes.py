from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.db import get_session
from backend.mailer import send_account_link
from backend.models import (
    AuthSession,
    PasswordResetToken,
    PlatformInvitation,
    PlatformRoleBinding,
    Tenant,
    TenantMembership,
    User,
    UserInvitation,
)
from backend.rate_limit import clear_rate_limit, enforce_rate_limit
from backend.schemas import (
    InvitationAcceptRequest,
    LoginRequest,
    PasswordForgotRequest,
    PasswordResetRequest,
)
from backend.security import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    Principal,
    create_session,
    current_principal,
    hash_password,
    token_hash,
    validate_password,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["authentication"])


def _set_auth_cookies(response: Response, raw_session: str, raw_csrf: str) -> None:
    settings = get_settings()
    max_age = settings.session_hours * 3600
    response.set_cookie(
        SESSION_COOKIE,
        raw_session,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        raw_csrf,
        max_age=max_age,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


async def _membership_payload(session: AsyncSession, user_id: str) -> list[dict]:
    rows = (
        await session.execute(
            select(TenantMembership, Tenant)
            .join(Tenant, Tenant.id == TenantMembership.tenant_id)
            .where(
                TenantMembership.user_id == user_id,
                TenantMembership.status == "active",
                Tenant.status == "active",
                Tenant.deleted_at.is_(None),
            )
        )
    ).all()
    return [
        {
            "tenant_id": tenant.id,
            "tenant_name": tenant.name,
            "kind": tenant.kind,
            "role": membership.role,
            "company_role_code": membership.company_role_code,
        }
        for membership, tenant in rows
    ]


@router.post("/login")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    email = (payload.username or payload.email or "").strip().lower()
    ip = request.client.host if request.client else "unknown"
    rate_key = f"login:{ip}:{email}"
    await enforce_rate_limit(rate_key, 8, 900)
    user = await session.scalar(select(User).where(or_(User.username == email, User.email == email), User.deleted_at.is_(None)))
    if (not user or user.status != "active" or user.account_type != "customer"
            or not verify_password(user.password_hash, payload.password)):
        raise HTTPException(401, "账号或密码错误")
    await clear_rate_limit(rate_key)
    from backend.onboarding import ensure_personal_space
    await ensure_personal_space(session, user)
    row = await session.execute(
        select(TenantMembership, Tenant)
        .join(Tenant, Tenant.id == TenantMembership.tenant_id)
        .where(
            TenantMembership.user_id == user.id,
            TenantMembership.status == "active",
            Tenant.status == "active",
            Tenant.deleted_at.is_(None),
        )
        .order_by(TenantMembership.workspace_kind.asc(), TenantMembership.created_at.asc())
        .limit(1)
    )
    membership_pair = row.first()
    if not membership_pair:
        raise HTTPException(403, "账号没有可用的企业成员关系")
    _, tenant = membership_pair
    raw_session, raw_csrf, _ = await create_session(
        session, user, tenant, request.headers.get("user-agent", ""), "customer"
    )
    user.last_login_at = datetime.now(UTC)
    await session.commit()
    _set_auth_cookies(response, raw_session, raw_csrf)
    return {"data": {"logged_in": True, "must_change_password": user.must_change_password}}


@router.post("/platform/login")
async def platform_login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    email = (payload.username or payload.email or "").strip().lower()
    ip = request.client.host if request.client else "unknown"
    rate_key = f"platform-login:{ip}:{email}"
    await enforce_rate_limit(rate_key, 8, 900)
    user = await session.scalar(select(User).where(User.email == email, User.deleted_at.is_(None)))
    bindings = [] if not user else list((await session.scalars(select(PlatformRoleBinding).where(
        PlatformRoleBinding.user_id == user.id, PlatformRoleBinding.status == "active",
    ))).all())
    if (not user or user.status != "active" or user.account_type != "platform" or not bindings
            or not verify_password(user.password_hash, payload.password)):
        raise HTTPException(401, "平台账号或密码错误")
    await clear_rate_limit(rate_key)
    raw_session, raw_csrf, _ = await create_session(
        session, user, None, request.headers.get("user-agent", ""), "platform"
    )
    user.last_login_at = datetime.now(UTC)
    await session.commit()
    _set_auth_cookies(response, raw_session, raw_csrf)
    return {"data": {"logged_in": True, "must_change_password": user.must_change_password}}


@router.get("/me")
async def me(user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    account = await session.get(User, user.user_id)
    return {
        "data": {
            "id": user.user_id,
            "email": account.email if account else None,
            "username": account.username if account else user.username,
            "phone": account.phone if account else None,
            "phone_verified": False,
            "display_name": user.display_name,
            "tenant_id": user.tenant_id,
            "tenant_name": user.tenant_name,
            "workspace_kind": user.workspace_kind,
            "email_verified": bool(account and account.email_verified_at),
            "company_creation_requires_verified_email": get_settings().require_verified_email_for_company,
            "account_type": user.account_type,
            "session_context": user.session_context,
            "role": user.role.value,
            "is_platform_admin": user.is_platform_admin,
            "company_role_code": user.company_role_code,
            "platform_roles": list(user.platform_roles),
            "memberships": await _membership_payload(session, user.user_id) if user.account_type == "customer" else [],
        }
    }


@router.post("/logout")
async def logout(
    response: Response,
    user: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
):
    auth = await session.get(AuthSession, user.session_id)
    if auth:
        auth.revoked_at = datetime.now(UTC)
    await session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"data": {"logged_out": True}}


@router.post("/invitations/{token}/accept")
async def accept_invitation(
    token: str,
    payload: InvitationAcceptRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    await enforce_rate_limit(f"invite:{request.client.host if request.client else 'unknown'}", 12, 900)
    from backend.onboarding import commit_identity, ensure_personal_space, identity_audit

    now = datetime.now(UTC)
    invitation = await session.scalar(update(UserInvitation).where(
        UserInvitation.token_hash == token_hash(token), UserInvitation.status == "pending",
        UserInvitation.expires_at > now,
    ).values(status="accepted", accepted_at=now).returning(UserInvitation))
    if not invitation:
        raise HTTPException(410, "邀请无效或已过期")
    tenant = await session.get(Tenant, invitation.tenant_id)
    if not tenant or tenant.kind != "company" or tenant.status != "active" or tenant.deleted_at is not None:
        raise HTTPException(410, "邀请所属企业不可用")
    user = await session.scalar(select(User).where(User.email == invitation.email).with_for_update())
    if not user and request.cookies.get(SESSION_COOKIE):
        principal = await current_principal(request, session)
        candidate = await session.scalar(select(User).where(User.id == principal.user_id).with_for_update())
        if not candidate or candidate.account_type != "customer" or candidate.email:
            raise HTTPException(403, "请使用受邀邮箱对应的账号；未绑定邮箱的账号可直接确认加入")
        user = candidate
        user.email = invitation.email
    if user and (user.deleted_at is not None or user.status != "active"):
        raise HTTPException(409, "该账号不可用，请联系管理员")
    if not user:
        validate_password(payload.password)
        if len(payload.display_name.strip()) < 2:
            raise HTTPException(422, "姓名至少两个字符")
        user = User(
            **({"username": payload.username.strip().lower()} if payload.username else {}),
            email=invitation.email,
            display_name=payload.display_name.strip(),
            password_hash=hash_password(payload.password),
            account_type="customer",
        )
        session.add(user)
        await session.flush()
    else:
        if user.account_type != "customer":
            raise HTTPException(409, "平台账号不能接受企业邀请")
        principal = await current_principal(request, session)
        if principal.user_id != user.id:
            raise HTTPException(403, "请登录受邀邮箱对应的账号后确认加入")
        cookie, header = request.cookies.get(CSRF_COOKIE), request.headers.get("X-CSRF-Token")
        if not cookie or not header or not secrets.compare_digest(cookie, header):
            raise HTTPException(403, "CSRF 校验失败")
        other_company = await session.scalar(select(TenantMembership).where(
            TenantMembership.user_id == user.id,
            TenantMembership.tenant_id != invitation.tenant_id,
            TenantMembership.status == "active",
            TenantMembership.workspace_kind == "company",
        ))
        if other_company:
            raise HTTPException(409, "一个账号只能属于一家公司，请使用该公司的专用邮箱账号")
    user.email_verified_at = now
    await ensure_personal_space(session, user)
    membership = await session.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == invitation.tenant_id,
            TenantMembership.user_id == user.id,
        )
    )
    if membership:
        raise HTTPException(409, "该账号已有公司成员记录，请在成员管理中处理")
    else:
        session.add(TenantMembership(
            tenant_id=tenant.id,
            user_id=user.id,
            role=invitation.role,
            company_role_code="company_admin" if invitation.role == "tenant_admin" else "company_member",
        ))
    invitation.status = "accepted"
    invitation.accepted_at = now
    session.add(identity_audit(request, user, tenant.id, "membership.activated", invitation.id))
    raw_session, raw_csrf, _ = await create_session(
        session, user, tenant, request.headers.get("user-agent", "")
    )
    await commit_identity(session)
    _set_auth_cookies(response, raw_session, raw_csrf)
    return {"data": {"accepted": True, "tenant_id": tenant.id}}


@router.post("/platform-invitations/{token}/accept")
async def accept_platform_invitation(
    token: str,
    payload: InvitationAcceptRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    await enforce_rate_limit(f"platform-invite:{request.client.host if request.client else 'unknown'}", 12, 900)
    validate_password(payload.password)
    invitation = await session.scalar(select(PlatformInvitation).where(
        PlatformInvitation.token_hash == token_hash(token)
    ))
    now = datetime.now(UTC)
    expiry = None if not invitation else invitation.expires_at
    if expiry and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    if not invitation or invitation.status != "pending" or not expiry or expiry <= now:
        raise HTTPException(410, "平台邀请无效或已过期")
    user = await session.scalar(select(User).where(User.email == invitation.email))
    if user:
        active_membership = await session.scalar(select(TenantMembership).where(
            TenantMembership.user_id == user.id, TenantMembership.status == "active"
        ))
        if user.account_type != "platform" or active_membership:
            raise HTTPException(409, "客户公司账号不能转换为平台账号")
        user.display_name = payload.display_name.strip()
        user.password_hash = hash_password(payload.password)
        user.status = "active"
    else:
        user = User(email=invitation.email, display_name=payload.display_name.strip(),
                    password_hash=hash_password(payload.password), account_type="platform")
        session.add(user)
        await session.flush()
    binding = await session.scalar(select(PlatformRoleBinding).where(
        PlatformRoleBinding.user_id == user.id,
        PlatformRoleBinding.role_code == invitation.role_code,
    ))
    if binding:
        binding.status = "active"
    else:
        session.add(PlatformRoleBinding(user_id=user.id, role_code=invitation.role_code,
                                        granted_by=invitation.invited_by))
    invitation.status = "accepted"
    invitation.accepted_at = now
    raw_session, raw_csrf, _ = await create_session(
        session, user, None, request.headers.get("user-agent", ""), "platform"
    )
    await session.commit()
    _set_auth_cookies(response, raw_session, raw_csrf)
    return {"data": {"accepted": True, "account_type": "platform"}}


@router.post("/password/forgot")
async def forgot_password(
    payload: PasswordForgotRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    email = payload.email.strip().lower()
    await enforce_rate_limit(f"forgot:{request.client.host if request.client else 'unknown'}:{email}", 5, 3600)
    user = await session.scalar(select(User).where(User.email == email, User.deleted_at.is_(None)))
    preview_url = None
    if user and user.status == "active" and user.email:
        raw = secrets.token_urlsafe(48)
        session.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=token_hash(raw),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        preview_url = f"{get_settings().frontend_base_url}/reset-password?token={quote(raw)}"
        await send_account_link(user.email, "password-reset", preview_url, session=session)
        await session.commit()
    data = {"message": "如果邮箱存在，重置链接将发送至该邮箱"}
    if get_settings().mail_debug and preview_url:
        data["preview_url"] = preview_url
    return {"data": data}


@router.post("/password/reset")
async def reset_password(
    payload: PasswordResetRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    await enforce_rate_limit(f"reset:{request.client.host if request.client else 'unknown'}", 10, 900)
    validate_password(payload.password)
    row = await session.scalar(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash(payload.token),
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > datetime.now(UTC),
        )
    )
    if not row:
        raise HTTPException(410, "重置链接无效或已过期")
    user = await session.get(User, row.user_id)
    if not user or user.deleted_at is not None:
        raise HTTPException(410, "重置链接无效或已过期")
    user.password_hash = hash_password(payload.password)
    user.must_change_password = False
    row.used_at = datetime.now(UTC)
    await session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await session.commit()
    return {"data": {"reset": True}}
