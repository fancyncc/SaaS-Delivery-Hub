from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.db import get_session
from backend.models import AuthSession, PlatformRoleBinding, Tenant, TenantMembership, User
from backend.schemas import Role

SESSION_COOKIE = "saas_session"
CSRF_COOKIE = "saas_csrf"
password_hasher = PasswordHasher(time_cost=2, memory_cost=65536, parallelism=2)


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(value: str) -> str:
    return password_hasher.hash(value)


def verify_password(hashed: str, value: str) -> bool:
    try:
        return password_hasher.verify(hashed, value)
    except (VerifyMismatchError, InvalidHashError):
        return False


def validate_password(value: str) -> None:
    if len(value) < 10 or len(value) > 128:
        raise HTTPException(422, "密码长度必须为 10–128 个字符")
    if not any(ch.isalpha() for ch in value) or not any(ch.isdigit() for ch in value):
        raise HTTPException(422, "密码必须同时包含字母和数字")


@dataclass(frozen=True)
class Principal:
    user_id: str
    subject: str
    email: str
    display_name: str
    tenant_id: str | None
    tenant_name: str | None
    account_type: str
    session_context: str
    role: Role
    is_platform_admin: bool
    company_role_code: str
    platform_roles: tuple[str, ...]
    session_id: str
    workspace_kind: str = "company"
    username: str = ""


async def create_session(
    session: AsyncSession,
    user: User,
    tenant: Tenant | None,
    device_summary: str = "",
    context_type: str = "customer",
) -> tuple[str, str, AuthSession]:
    raw_session = secrets.token_urlsafe(48)
    raw_csrf = secrets.token_urlsafe(32)
    row = AuthSession(
        user_id=user.id,
        tenant_id=tenant.id if tenant else None,
        context_type=context_type,
        token_hash=token_hash(raw_session),
        csrf_hash=token_hash(raw_csrf),
        device_summary=device_summary[:255],
        expires_at=datetime.now(UTC) + timedelta(hours=get_settings().session_hours),
    )
    session.add(row)
    await session.flush()
    return raw_session, raw_csrf, row


async def current_principal(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Principal:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        raise HTTPException(401, "请先登录")
    auth = await session.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == token_hash(raw),
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > datetime.now(UTC),
        )
    )
    if not auth:
        raise HTTPException(401, "登录会话已失效")
    user = await session.get(User, auth.user_id)
    if not user or user.status != "active" or user.deleted_at is not None:
        raise HTTPException(401, "账号不可用")
    auth.last_seen_at = datetime.now(UTC)
    platform_roles = tuple((await session.scalars(select(PlatformRoleBinding.role_code).where(
        PlatformRoleBinding.user_id == user.id,
        PlatformRoleBinding.status == "active",
    ))).all())
    is_platform_admin = bool(platform_roles)
    tenant = None
    membership = None
    if auth.context_type == "platform":
        if user.account_type != "platform" or not platform_roles or auth.tenant_id is not None:
            raise HTTPException(401, "平台会话已失效")
        role = Role.PLATFORM_ADMIN
    else:
        tenant = await session.get(Tenant, auth.tenant_id) if auth.tenant_id else None
        membership = await session.scalar(select(TenantMembership).where(
            TenantMembership.user_id == auth.user_id,
            TenantMembership.tenant_id == auth.tenant_id,
            TenantMembership.status == "active",
        ))
        if (user.account_type != "customer" or not tenant or tenant.status != "active"
                or tenant.deleted_at is not None or not membership):
            raise HTTPException(401, "账号或租户不可用")
        if tenant.kind == "personal" and tenant.personal_owner_id != user.id:
            raise HTTPException(401, "个人空间不可用")
        role = Role.TENANT_ADMIN if membership.company_role_code == "company_admin" else Role.TENANT_MEMBER
    if session.bind and session.bind.dialect.name == "postgresql":
        session.info["rls_context"] = {
            "app.current_user_id": user.id,
            "app.current_tenant_id": tenant.id if tenant else "",
            "app.current_company_role": membership.company_role_code if membership else "",
            "app.is_platform_admin": "true" if is_platform_admin else "false",
            "app.platform_roles": ",".join(platform_roles),
        }
        await session.execute(text("SELECT set_config('app.current_user_id', :value, true)"), {"value": user.id})
        await session.execute(text("SELECT set_config('app.current_tenant_id', :value, true)"), {"value": tenant.id if tenant else ""})
        await session.execute(text("SELECT set_config('app.current_company_role', :value, true)"), {"value": membership.company_role_code if membership else ""})
        await session.execute(text("SELECT set_config('app.is_platform_admin', :value, true)"), {"value": "true" if is_platform_admin else "false"})
        await session.execute(text("SELECT set_config('app.platform_roles', :value, true)"), {"value": ",".join(platform_roles)})
    return Principal(
        user_id=user.id,
        subject=user.email or user.username,
        email=user.email or "",
        display_name=user.display_name,
        tenant_id=tenant.id if tenant else None,
        tenant_name=tenant.name if tenant else None,
        account_type=user.account_type,
        session_context=auth.context_type,
        role=role,
        is_platform_admin=is_platform_admin,
        company_role_code=membership.company_role_code if membership else "",
        platform_roles=platform_roles,
        session_id=auth.id,
        workspace_kind=tenant.kind if tenant else "company",
        username=user.username,
    )


def require_roles(*roles: Role):
    async def dependency(user: Principal = Depends(current_principal)) -> Principal:
        if user.is_platform_admin and Role.PLATFORM_ADMIN in roles:
            return user
        if user.role not in roles:
            raise HTTPException(403, "权限不足")
        return user

    return dependency


async def require_admin(user: Principal = Depends(current_principal)) -> Principal:
    if not user.is_platform_admin and user.company_role_code != "company_admin":
        raise HTTPException(403, "需要管理员权限")
    return user


def require_platform_roles(*roles: str):
    async def dependency(user: Principal = Depends(current_principal)) -> Principal:
        if user.session_context != "platform" or not set(roles).intersection(user.platform_roles):
            raise HTTPException(403, "需要相应的平台人员权限")
        return user
    return dependency


async def require_platform_staff(user: Principal = Depends(current_principal)) -> Principal:
    if user.session_context != "platform" or not user.platform_roles:
        raise HTTPException(403, "仅限平台人员")
    return user


async def require_company_admin(user: Principal = Depends(current_principal)) -> Principal:
    if user.session_context != "customer" or user.company_role_code != "company_admin":
        raise HTTPException(403, "只有公司管理员可以执行该操作")
    return user


async def require_enterprise_admin(user: Principal = Depends(require_company_admin)) -> Principal:
    if user.workspace_kind != "company":
        raise HTTPException(403, "请先切换到公司空间")
    return user


async def idempotency_key(value: str | None = Header(None, alias="Idempotency-Key")) -> str:
    if not value or len(value) > 160:
        raise HTTPException(400, "需要有效的 Idempotency-Key 请求头")
    return value
