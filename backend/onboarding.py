from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit import audit_event
from backend.auth_routes import _membership_payload, _set_auth_cookies
from backend.config import get_settings
from backend.db import get_session
from backend.mailer import send_account_link
from backend.models import (
    AuditEvent,
    AuthSession,
    EmailVerification,
    Tenant,
    TenantMembership,
    User,
)
from backend.rate_limit import enforce_rate_limit
from backend.security import (
    Principal,
    create_session,
    current_principal,
    hash_password,
    token_hash,
    validate_password,
    verify_password,
)

router = APIRouter(tags=["self-service onboarding"])


class EmailRequest(BaseModel):
    email: str = Field(min_length=5, max_length=160, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

    @field_validator("email", mode="before")
    @classmethod
    def normalize(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("邮箱必须为字符串")
        return value.strip().lower()


class VerifyRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    display_name: str = Field(default="", max_length=100)
    password: str = Field(default="", max_length=128)


class AccountRegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[a-z][a-z0-9_.-]*$")
    password: str = Field(min_length=10, max_length=128)
    display_name: str = Field(default="", max_length=100)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value):
        if not isinstance(value, str):
            raise ValueError("账号必须为字符串")
        return value.strip().lower()


class CompanyRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    slug: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        if len(value.strip()) < 2:
            raise ValueError("公司名称至少两个字符")
        return value.strip()


class ProfileUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    username: str | None = Field(default=None, min_length=3, max_length=40, pattern=r"^[a-z][a-z0-9_.-]*$")
    phone: str | None = Field(default=None, max_length=40)
    current_password: str = Field(default="", max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value):
        if not value or not value.strip():
            return None
        number = re.sub(r"[\s()-]", "", value)
        if re.fullmatch(r"1[3-9]\d{9}", number):
            number = "+86" + number
        if not re.fullmatch(r"\+[1-9]\d{6,14}", number):
            raise ValueError("请填写有效手机号；非中国大陆号码需包含国际区号")
        return number


class BindEmailRequest(EmailRequest):
    current_password: str = Field(min_length=1, max_length=128)


@router.get("/api/auth/profile")
async def profile(principal: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    user = await session.get(User, principal.user_id)
    if not user or user.account_type != "customer":
        raise HTTPException(403, "仅限个人账号")
    return {"data": {"username": user.username, "display_name": user.display_name,
                     "email": user.email, "email_verified": bool(user.email_verified_at),
                     "phone": user.phone, "phone_verified": False,
                     "created_at": user.created_at.isoformat()}}


@router.patch("/api/auth/profile")
async def update_profile(payload: ProfileUpdateRequest, request: Request,
                         principal: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    if principal.account_type != "customer":
        raise HTTPException(403, "仅限个人账号")
    await enforce_rate_limit(f"profile:{principal.user_id}", 20, 3600)
    user = await session.scalar(select(User).where(User.id == principal.user_id).with_for_update())
    if not user:
        raise HTTPException(401, "账号不可用")
    if {"username", "phone"}.intersection(payload.model_fields_set):
        if not verify_password(user.password_hash, payload.current_password):
            raise HTTPException(403, "当前密码不正确")
    changed = []
    if payload.username and payload.username != user.username:
        if await session.scalar(select(User.id).where(User.username == payload.username, User.id != user.id)):
            raise HTTPException(409, "该账号名称已被使用")
        user.username = payload.username
        changed.append("username")
    if "phone" in payload.model_fields_set:
        if payload.phone and await session.scalar(select(User.id).where(User.phone == payload.phone, User.id != user.id)):
            raise HTTPException(409, "该手机号已被其他账号填写")
        user.phone = payload.phone
        changed.append("phone")
    if payload.display_name is not None:
        if not payload.display_name.strip():
            raise HTTPException(422, "昵称不能为空")
        user.display_name = payload.display_name.strip()
        changed.append("display_name")
    session.add(audit_event(request, principal, "account.profile_updated", user.id, {"fields": changed}))
    await commit_identity(session)
    return {"data": {"saved": True}}


@router.post("/api/auth/profile/email")
async def bind_email(payload: BindEmailRequest, principal: Principal = Depends(current_principal),
                     session: AsyncSession = Depends(get_session)):
    if principal.account_type != "customer":
        raise HTTPException(403, "仅限个人账号")
    await enforce_rate_limit(f"email-bind:{principal.user_id}", 5, 3600)
    user = await session.scalar(select(User).where(User.id == principal.user_id).with_for_update())
    if not user or not verify_password(user.password_hash, payload.current_password):
        raise HTTPException(403, "当前密码不正确")
    # Keep existing enterprise email associations stable; replacement is a separate recovery flow.
    if user.email and user.email != payload.email:
        raise HTTPException(409, "已有绑定邮箱，当前入口仅支持新增绑定或验证已有邮箱")
    if await session.scalar(select(User.id).where(User.email == payload.email, User.id != user.id)):
        raise HTTPException(409, "该邮箱已被其他账号绑定")
    if user.email_verified_at:
        return {"data": {"verified": True}}
    return {"data": await issue_verification(session, payload.email, user.id)}


async def commit_identity(session: AsyncSession) -> None:
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, "信息已存在或发生并发变更，请刷新后重试") from exc


def identity_audit(request: Request, user: User, tenant_id: str | None, event: str, resource_id: str) -> AuditEvent:
    return AuditEvent(tenant_id=tenant_id, actor=user.email or user.username, actor_user_id=user.id, event_type=event,
                      resource_id=resource_id, request_id=getattr(request.state, "request_id", ""),
                      ip_hash=token_hash(request.client.host if request.client else "unknown"))


async def active_company(session: AsyncSession, user_id: str) -> TenantMembership | None:
    return await session.scalar(select(TenantMembership).where(
        TenantMembership.user_id == user_id, TenantMembership.status == "active",
        TenantMembership.workspace_kind == "company",
    ))


async def ensure_personal_space(session: AsyncSession, user: User) -> Tenant:
    tenant = await session.scalar(select(Tenant).where(Tenant.personal_owner_id == user.id))
    if tenant:
        return tenant
    tenant = Tenant(name=f"{user.display_name}的个人空间", slug=f"personal-{user.id}",
                    kind="personal", personal_owner_id=user.id)
    session.add(tenant)
    await session.flush()
    session.add(TenantMembership(tenant_id=tenant.id, user_id=user.id, workspace_kind="personal",
                                 role="tenant_admin", company_role_code="company_admin"))
    await session.flush()
    return tenant


async def issue_verification(session: AsyncSession, email: str, user_id: str | None = None) -> dict:
    raw = secrets.token_urlsafe(48)
    if user_id:
        await session.execute(update(EmailVerification).where(EmailVerification.user_id == user_id,
            EmailVerification.consumed_at.is_(None)).values(consumed_at=datetime.now(UTC)))
    session.add(EmailVerification(email=email, user_id=user_id, token_hash=token_hash(raw),
                                  expires_at=datetime.now(UTC) + timedelta(hours=1)))
    url = f"{get_settings().frontend_base_url}/verify-email?token={quote(raw)}"
    await send_account_link(email, "email_verification", url, session=session)
    await commit_identity(session)
    return {"sent": True, **({"verification_url": url} if get_settings().mail_debug else {})}


@router.post("/api/auth/register")
async def register(payload: AccountRegisterRequest | EmailRequest, request: Request, response: Response,
                   session: AsyncSession = Depends(get_session)):
    ip = request.client.host if request.client else "unknown"
    await enforce_rate_limit(f"register:ip:{ip}", 20, 3600)
    if isinstance(payload, AccountRegisterRequest):
        validate_password(payload.password)
        if await session.scalar(select(User.id).where(User.username == payload.username)):
            raise HTTPException(409, "该账号已被使用，请换一个账号名称")
        user = User(username=payload.username, display_name=payload.display_name.strip() or payload.username,
                    password_hash=hash_password(payload.password))
        session.add(user)
        await session.flush()
        tenant = await ensure_personal_space(session, user)
        raw, csrf, _ = await create_session(session, user, tenant)
        session.add(identity_audit(request, user, tenant.id, "account.registered", user.id))
        await commit_identity(session)
        _set_auth_cookies(response, raw, csrf)
        return {"data": {"registered": True, "username": user.username}}
    # Retain legacy email-link registration for outstanding integrations.
    await enforce_rate_limit(f"register:email:{payload.email}", 3, 3600)
    if await session.scalar(select(User.id).where(User.email == payload.email)):
        raise HTTPException(409, "该邮箱已注册，请登录或找回密码")
    return {"data": await issue_verification(session, payload.email)}


@router.post("/api/auth/email-verification")
async def request_verification(user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    if user.account_type != "customer":
        raise HTTPException(403, "仅限客户账号")
    if not user.email:
        raise HTTPException(422, "请先在个人主页填写要绑定的邮箱")
    await enforce_rate_limit(f"verify-send:{user.user_id}", 3, 3600)
    return {"data": await issue_verification(session, user.email, user.user_id)}


@router.post("/api/auth/verify-email")
async def verify_email(payload: VerifyRequest, request: Request, response: Response,
                       session: AsyncSession = Depends(get_session)):
    await enforce_rate_limit(f"verify:{request.client.host if request.client else 'unknown'}", 20, 900)
    now = datetime.now(UTC)
    # Conditional UPDATE consumes the token atomically on PostgreSQL and SQLite.
    item = await session.scalar(update(EmailVerification).where(
        EmailVerification.token_hash == token_hash(payload.token),
        EmailVerification.consumed_at.is_(None), EmailVerification.expires_at > now,
    ).values(consumed_at=now).returning(EmailVerification))
    if not item:
        raise HTTPException(410, "验证链接无效或已过期")
    if item.user_id:
        principal = await current_principal(request, session)
        if principal.user_id != item.user_id or principal.account_type != "customer":
            raise HTTPException(403, "请登录申请绑定的账号后验证")
        account = await session.scalar(select(User).where(User.id == item.user_id).with_for_update())
        if not account or (account.email and account.email != item.email):
            raise HTTPException(409, "账号邮箱已变化，请重新申请绑定")
        if await session.scalar(select(User.id).where(User.email == item.email, User.id != account.id)):
            raise HTTPException(409, "该邮箱已被其他账号绑定")
        account.email, account.email_verified_at = item.email, now
        session.add(identity_audit(request, account, principal.tenant_id, "account.email_bound", account.id))
        await commit_identity(session)
        return {"data": {"verified": True}}
    user = await session.scalar(select(User).where(User.email == item.email).with_for_update())
    if user:
        principal = await current_principal(request, session)
        if principal.user_id != user.id or user.account_type != "customer":
            raise HTTPException(403, "请登录该邮箱对应的客户账号后验证")
        user.email_verified_at = now
        session.add(identity_audit(request, user, principal.tenant_id, "account.email_verified", user.id))
        await commit_identity(session)
        return {"data": {"verified": True}}
    validate_password(payload.password)
    if len(payload.display_name.strip()) < 2:
        raise HTTPException(422, "姓名至少两个字符")
    user = User(email=item.email, display_name=payload.display_name.strip(),
                password_hash=hash_password(payload.password), email_verified_at=now)
    session.add(user)
    try:
        await session.flush()
        tenant = await ensure_personal_space(session, user)
        session.add(identity_audit(request, user, tenant.id, "account.registered", user.id))
        raw, csrf, _ = await create_session(session, user, tenant)
        await commit_identity(session)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, "该邮箱已注册，请登录") from exc
    _set_auth_cookies(response, raw, csrf)
    return {"data": {"verified": True}}


@router.get("/api/auth/spaces")
async def spaces(user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    if user.account_type != "customer":
        raise HTTPException(403, "仅限客户账号")
    return {"data": await _membership_payload(session, user.user_id)}


@router.post("/api/auth/spaces/{space_id}/switch")
async def switch_space(space_id: UUID, request: Request, response: Response,
                       principal: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    tenant = await session.get(Tenant, str(space_id))
    membership = await session.scalar(select(TenantMembership).where(
        TenantMembership.tenant_id == str(space_id), TenantMembership.user_id == principal.user_id,
        TenantMembership.status == "active"))
    if (principal.account_type != "customer" or not tenant or not membership
            or tenant.status != "active" or tenant.deleted_at is not None
            or (tenant.kind == "personal" and tenant.personal_owner_id != principal.user_id)):
        raise HTTPException(404, "空间不可用")
    user = await session.get(User, principal.user_id)
    auth = await session.get(AuthSession, principal.session_id)
    if not user or not auth:
        raise HTTPException(401, "会话不可用")
    auth.revoked_at = datetime.now(UTC)
    raw, csrf, _ = await create_session(session, user, tenant)
    session.add(audit_event(request, principal, "space.switched", tenant.id))
    await commit_identity(session)
    _set_auth_cookies(response, raw, csrf)
    return {"data": {"tenant_id": tenant.id}}


@router.get("/api/companies/name-check")
async def company_name_check(name: str, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    if user.account_type != "customer":
        raise HTTPException(403, "仅限客户账号")
    exists = await session.scalar(select(Tenant.id).where(Tenant.kind == "company", Tenant.name == name.strip(),
                                                         Tenant.deleted_at.is_(None)).limit(1))
    return {"data": {"similar_name_exists": bool(exists), "identity_verified": False}}


@router.post("/api/companies")
async def create_company(payload: CompanyRequest, request: Request, principal: Principal = Depends(current_principal),
                         session: AsyncSession = Depends(get_session)):
    if principal.account_type != "customer":
        raise HTTPException(403, "仅限客户账号")
    await enforce_rate_limit(f"company-create:{principal.user_id}", 5, 3600)
    user = await session.scalar(select(User).where(User.id == principal.user_id).with_for_update())
    if not user:
        raise HTTPException(401, "账号不可用")
    if get_settings().require_verified_email_for_company and not user.email_verified_at:
        raise HTTPException(403, "请先验证邮箱")
    if await active_company(session, user.id):
        raise HTTPException(409, "一个账号最多加入一家企业")
    if payload.slug.startswith("personal-") or await session.scalar(select(Tenant.id).where(Tenant.slug == payload.slug)):
        raise HTTPException(409, "空间标识已被占用或保留")
    tenant = Tenant(name=payload.name, slug=payload.slug, kind="company")
    session.add(tenant)
    try:
        await session.flush()
        session.add(TenantMembership(tenant_id=tenant.id, user_id=user.id, role="tenant_admin", company_role_code="company_admin"))
        session.add(audit_event(request, principal, "company.self_created", tenant.id))
        await commit_identity(session)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, "公司或成员信息已存在") from exc
    return {"data": {"id": tenant.id, "name": tenant.name}}
