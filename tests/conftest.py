import os
from pathlib import Path

TEST_DB = Path(__file__).parent / "test.db"
os.environ["SAAS_ENV_FILE"] = str(Path(__file__).resolve().parents[1] / "config/rag.mock.env.example")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB.as_posix()}"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6399/15"
os.environ["MODEL_MODE"] = "deterministic"
os.environ["RAG_MODE"] = "mock"
os.environ["BOOTSTRAP_ADMIN_EMAIL"] = "admin@example.com"
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "ChangeMe123!"

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from backend.db import SessionLocal, bootstrap_identity, engine
from backend.main import app
from backend.models import Base, Tenant, TenantMembership, User
from backend.rate_limit import _fallback
from backend.retrieval_sources_models import RetrievalBase
from backend.security import hash_password


@pytest.fixture(autouse=True)
async def database():
    _fallback.clear()
    async with engine.begin() as conn:
        await conn.run_sync(RetrievalBase.metadata.drop_all)
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(RetrievalBase.metadata.create_all)
    await bootstrap_identity()
    async with SessionLocal() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == "legacy-demo"))
        company_admin = User(
            email="company-admin@example.com", display_name="Legacy 公司管理员",
            password_hash=hash_password("CompanyAdmin123"), account_type="customer",
        )
        session.add(company_admin)
        await session.flush()
        session.add(TenantMembership(
            tenant_id=tenant.id, user_id=company_admin.id, role="tenant_admin",
            company_role_code="company_admin",
        ))
        await session.commit()
    yield


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as value:
        response = await value.post("/api/auth/login", json={"email": "company-admin@example.com", "password": "CompanyAdmin123"})
        assert response.status_code == 200
        value.headers["X-CSRF-Token"] = value.cookies.get("saas_csrf")
        yield value


@pytest.fixture
async def platform_client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as value:
        response = await value.post(
            "/api/auth/platform/login",
            json={"email": "admin@example.com", "password": "ChangeMe123!"},
        )
        assert response.status_code == 200
        value.headers["X-CSRF-Token"] = value.cookies.get("saas_csrf")
        yield value
