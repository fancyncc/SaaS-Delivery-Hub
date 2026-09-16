"""Opt-in acceptance on a disposable database, never the application database.

AGENT_TEST_POSTGRES_URL must point to database agent_rag_test. Embedding provider
responses are deterministic test vectors; storage, SQL, RLS and recovery are real.
"""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

import psycopg
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def prepare():
    raw = os.environ["AGENT_TEST_POSTGRES_URL"]
    url = make_url(raw)
    if url.database != "agent_rag_test" or url.host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("Only a local disposable agent_rag_test database is allowed")
    owner = url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)
    env = {**os.environ, "DATABASE_URL": owner}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True)
    with psycopg.connect(raw, autocommit=True) as connection:
        if not connection.execute("SELECT 1 FROM pg_roles WHERE rolname='rag_test_app'").fetchone():
            connection.execute("CREATE ROLE rag_test_app LOGIN NOSUPERUSER NOBYPASSRLS")
        connection.execute("GRANT USAGE ON SCHEMA public TO rag_test_app")
        connection.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO rag_test_app")
        version = connection.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()[0]
        assert tuple(int(x) for x in version.split('.')[:2]) >= (0, 8), version
    os.environ["DATABASE_URL"] = url.set(drivername="postgresql+psycopg", username="rag_test_app", password=None).render_as_string(hide_password=False)
    os.environ["AGENT_ENGINE"] = "v2"
    os.environ["EXECUTION_MODE"] = "worker"
    os.environ["REDIS_URL"] = "redis://127.0.0.1:6399/15"
    return owner


async def check(owner):
    import uuid

    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from test_api import project

    from backend import knowledge, worker
    from backend.agent_models import AgentAction, KnowledgePiece
    from backend.config import get_settings
    from backend.db import SessionLocal, engine
    from backend.main import app
    from backend.models import (
        AgentRun,
        KnowledgeDocument,
        Tenant,
        TenantMembership,
        User,
        WorkflowOutbox,
    )
    from backend.security import hash_password

    owner_engine = create_async_engine(owner)
    factory = async_sessionmaker(owner_engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:8]
    async with factory() as session:
        first, other = Tenant(name="RAG Test", slug=f"rag-{suffix}"), Tenant(name="Other", slug=f"other-{suffix}")
        user = User(email=f"rag-{suffix}@example.test", display_name="RAG Test", password_hash=hash_password("TestPassword123"), account_type="customer")
        session.add_all([first, other, user])
        await session.flush()
        session.add(TenantMembership(tenant_id=first.id, user_id=user.id, role="tenant_admin", company_role_code="company_admin"))
        await session.commit()

    async def vectors(value):
        result = [0.0] * 512
        result[0 if any(x in value for x in ("成员", "导入", "用户")) else 1] = 1.0
        return result
    original_embed = knowledge.embed
    knowledge.embed = vectors
    get_settings().embedding_model = "test-vector-512"
    try:
        async with factory() as session:
            for tenant, version, body in [(first, 1, "成员导入旧版本已被替代"), (first, 2, "成员导入必须使用有效邮箱并经过审批"), (other, 1, "成员导入跨企业独有秘密")]:
                doc = KnowledgeDocument(tenant_id=tenant.id, title="成员导入指南", version=version, module="import", body=body, source="original", license="test")
                session.add(doc)
                await session.flush()
                await knowledge.index_document(session, doc)
            await session.commit()
        async with SessionLocal() as session:
            # RLS is exercised as a non-owner, NOBYPASSRLS role.
            assert list(await session.scalars(select(KnowledgePiece))) == []
            await session.execute(text("SELECT set_config('app.current_tenant_id', :tenant, true)"), {"tenant": first.id})
            hits = await knowledge.retrieve(session, first.id, "用户批量导入")
            assert len(hits) == 1 and hits[0]["version"] == 2, hits
            assert "秘密" not in hits[0]["text"]
            assert not await knowledge.retrieve(session, first.id, "星际旅行量子推进器")
            hidden = list(await session.scalars(select(KnowledgePiece).where(KnowledgePiece.tenant_id == other.id)))
            assert hidden == []
            stored = await session.scalar(select(KnowledgePiece).where(KnowledgePiece.tenant_id == first.id))
            assert len(stored.embedding) == 512

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/auth/login", json={"email": user.email, "password": "TestPassword123"})
            assert response.status_code == 200, response.text
            client.headers["X-CSRF-Token"] = client.cookies.get("saas_csrf")
            p = await project(client)
            response = await client.post(f"/api/projects/{p['id']}/runs", headers={"Idempotency-Key": str(uuid.uuid4())})
            assert response.status_code == 200, response.text
            run_id = response.json()["data"]["id"]
            async with factory() as session:
                event = await session.scalar(select(WorkflowOutbox).where(WorkflowOutbox.run_id == run_id))
                event_id = event.id
            original = worker.advance
            calls = 0
            async def interrupted(session, run, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise ConnectionError("injected process boundary failure")
                return await original(session, run, **kwargs)
            worker.advance = interrupted
            try:
                await worker.process_event(event_id)
            finally:
                worker.advance = original
            async with factory() as session:
                assert len(list(await session.scalars(select(AgentAction).where(AgentAction.run_id == run_id)))) == 1
            await worker.process_event(event_id, raise_errors=True)
            await worker.process_event(event_id, raise_errors=True)
            async with factory() as session:
                run = await session.get(AgentRun, run_id)
                assert run.status == "waiting_approval", run.state
                actions = list(await session.scalars(select(AgentAction).where(AgentAction.run_id == run_id)))
                assert len(actions) == 5 and all(a.status == "succeeded" for a in actions)
        print("PASS: PostgreSQL migrations, vector(512), filtered hybrid retrieval, RLS and committed-round recovery")
    finally:
        knowledge.embed = original_embed
        await engine.dispose()
        await owner_engine.dispose()


if __name__ == "__main__":
    owner = prepare()
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(check(owner))
    else:
        asyncio.run(check(owner))
