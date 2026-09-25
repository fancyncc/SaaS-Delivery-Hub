from collections.abc import AsyncIterator

from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from backend import (
    agent_models,  # noqa: F401 -- register durable agent tables
    chat_models,  # noqa: F401 -- register private chat tables
    rag_v3_models,  # noqa: F401
)
from backend.config import get_settings
from backend.models import Base, PlatformRoleBinding, Tenant, TenantMembership, User
from backend.retrieval_sources_models import RetrievalBase

engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


@event.listens_for(Session, "after_begin")
def restore_request_scope(session, transaction, connection):
    # SET LOCAL expires on commit. Restore only the authenticated request's
    # scope before post-commit refreshes and subsequent statements.
    if connection.dialect.name == "postgresql":
        for name, value in session.info.get("rls_context", {}).items():
            connection.execute(text("SELECT set_config(:name, :value, true)"), {"name": name, "value": value})


async def init_db() -> None:
    if get_settings().auto_create_schema:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.run_sync(RetrievalBase.metadata.create_all)


async def bootstrap_identity() -> None:
    # Local import avoids a module cycle: security dependencies use get_session.
    from backend.permissions import ensure_permission_catalog
    from backend.security import hash_password

    settings = get_settings()
    async with SessionLocal() as session:
        # SQLite/dev create_all has no Alembic seed step. Production permission
        # mappings are migration-owned and must not be silently rewritten by API code.
        if session.bind and session.bind.dialect.name == "sqlite":
            await ensure_permission_catalog(session)
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == "legacy-demo"))
        if not tenant:
            tenant = Tenant(name=settings.legacy_tenant_name, slug="legacy-demo")
            session.add(tenant)
            await session.flush()
        email = settings.bootstrap_admin_email.strip().lower()
        user = await session.scalar(select(User).where(User.email == email))
        if not user:
            user = User(
                email=email,
                display_name=settings.bootstrap_admin_name,
                password_hash=hash_password(settings.bootstrap_admin_password),
                is_platform_admin=True,
                account_type="platform",
                must_change_password=True,
            )
            session.add(user)
            await session.flush()
        else:
            user.account_type = "platform"
            user.is_platform_admin = True
        membership = await session.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant.id,
                TenantMembership.user_id == user.id,
            )
        )
        if membership and membership.status == "active":
            membership.status = "disabled"
        binding = await session.scalar(select(PlatformRoleBinding).where(
            PlatformRoleBinding.user_id == user.id,
            PlatformRoleBinding.role_code == "platform_super_admin",
        ))
        if not binding:
            session.add(PlatformRoleBinding(
                user_id=user.id, role_code="platform_super_admin", granted_by=user.id,
            ))
        await session.commit()


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
