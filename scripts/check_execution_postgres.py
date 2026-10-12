"""Disposable PostgreSQL migration, RLS, concurrent Run and immutable snapshot checks.

EXECUTION_TEST_POSTGRES_URL must point to a local saas_acceptance_* database.
The caller creates the disposable database. Credentials never appear in reports.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))


def prepare(raw: str, app_password: str) -> tuple[str, str]:
    url = make_url(raw)
    if (not (url.database or "").startswith("saas_acceptance_")
            or url.host not in {"localhost", "127.0.0.1"}):
        raise ValueError("Use a local disposable saas_acceptance_* database")
    owner = url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)
    environment = {**os.environ, "DATABASE_URL": owner, "SAAS_ENV_FILE": str(ROOT / "data/acceptance/no-env-file")}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=environment, check=True)
    role = "execution_acceptance_app"
    with psycopg.connect(url.set(drivername="postgresql").render_as_string(hide_password=False)) as connection:
        connection.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD {}").format(
            sql.Identifier(role), sql.Literal(app_password)))
        connection.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
        connection.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(role)))
        connection.execute(sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(sql.Identifier(role)))
        from langgraph.checkpoint.postgres import PostgresSaver
        # Checkpoint DDL is owned by migration credentials, never by the Worker.
        with PostgresSaver.from_conn_string(url.set(drivername="postgresql").render_as_string(hide_password=False)) as saver:
            saver.setup()
        connection.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(role)))
    app = url.set(drivername="postgresql+psycopg", username=role, password=app_password).render_as_string(hide_password=False)
    os.environ.update({"DATABASE_URL": app, "APP_DB_USER": role, "APP_DB_PASSWORD": app_password,
                      "MIGRATION_DATABASE_URL": "", "SAAS_ENV_FILE": environment["SAAS_ENV_FILE"],
                      "MODEL_MODE": "deterministic", "RAG_MODE": "mock", "EXECUTION_MODE": "worker",
                      "MAIL_DEBUG": "true", "STORAGE_BACKEND": "database"})
    return owner, app


async def check(owner: str, app: str) -> dict:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import select, text
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from test_api import project

    from backend.db import SessionLocal, engine
    from backend.main import app as api
    from backend.models import AgentRun, Project, Tenant, TenantMembership, User, WorkflowOutbox
    from backend.run_snapshots import RunSnapshot
    from backend.runtime_guard import check_runtime_role
    from backend.security import hash_password
    from backend.worker import process_event

    owner_engine = create_async_engine(owner, poolclass=NullPool)
    factory = async_sessionmaker(owner_engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:8]
    tenants = [Tenant(name="Acceptance A", slug=f"accept-a-{suffix}"),
               Tenant(name="Acceptance B", slug=f"accept-b-{suffix}")]
    users = [User(email=f"accept-{i}-{suffix}@example.test", display_name="Acceptance",
                  password_hash=hash_password("AcceptanceOnly123!"), account_type="customer") for i in range(2)]
    try:
        async with factory() as session:
            session.add_all([*tenants, *users])
            await session.flush()
            for tenant, user in zip(tenants, users, strict=True):
                session.add(TenantMembership(tenant_id=tenant.id, user_id=user.id,
                    role="tenant_admin", company_role_code="company_admin"))
            await session.commit()
        async with engine.connect() as connection:
            guard = await check_runtime_role(connection)
        async with AsyncClient(transport=ASGITransport(api), base_url="http://test") as client:
            logged = await client.post("/api/auth/login", json={"email": users[0].email, "password": "AcceptanceOnly123!"})
            assert logged.status_code == 200, "LOGIN_FAILED"
            client.headers["X-CSRF-Token"] = client.cookies.get("saas_csrf")
            p = await project(client)
            async def start():
                return await client.post(f"/api/projects/{p['id']}/runs", headers={"Idempotency-Key": uuid.uuid4().hex})
            replies = await asyncio.gather(start(), start())
            assert sorted(reply.status_code for reply in replies) == [200, 409], "CONCURRENT_RUN_NOT_REJECTED"
            run_id = next(reply for reply in replies if reply.status_code == 200).json()["data"]["id"]
        async with factory() as session:
            event = await session.scalar(select(WorkflowOutbox).where(WorkflowOutbox.run_id == run_id))
            event_id = event.id
        await process_event(event_id, raise_errors=True)
        await process_event(event_id, raise_errors=True)
        async with factory() as session:
            assert (await session.get(AgentRun, run_id)).status == "waiting_approval", "WORKER_FAILED"
            first_snapshots = list(await session.scalars(select(RunSnapshot).where(RunSnapshot.run_id == run_id)))
            assert first_snapshots and any(s.kind == "approval" for s in first_snapshots)
            other = Project(tenant_id=tenants[1].id, name="Other", customer_name="Other",
                            created_by=users[1].id, requirements_text="Other tenant private requirement")
            session.add(other)
            await session.commit()
        # Reuse a pooled Session through commit/rollback and switch complete contexts.
        async with SessionLocal() as session:
            assert list(await session.scalars(select(RunSnapshot))) == [], "NO_CONTEXT_LEAK"
            for index in (0, 1, 0):
                await session.rollback()
                session.info["rls_context"] = {"app.current_user_id": users[index].id,
                    "app.current_tenant_id": tenants[index].id, "app.current_company_role": "company_admin",
                    "app.platform_roles": "", "app.is_platform_admin": "false"}
                snapshots = list(await session.scalars(select(RunSnapshot)))
                assert all(s.tenant_id == tenants[index].id for s in snapshots), "POOL_TENANT_LEAK"
                assert bool(snapshots) == (index == 0)
                await session.commit()
                assert all(s.tenant_id == tenants[index].id for s in await session.scalars(select(RunSnapshot)))
            for statement in ("UPDATE run_snapshots SET payload = '{}' WHERE run_id = :id",
                              "DELETE FROM run_snapshots WHERE run_id = :id",
                              "UPDATE agent_runs SET snapshot_version = 0 WHERE id = :id"):
                await session.rollback()
                try:
                    await session.execute(text(statement), {"id": run_id})
                except DBAPIError:
                    await session.rollback()
                else:
                    raise AssertionError("SNAPSHOT_MUTATION_ALLOWED")
            await session.execute(text("UPDATE implementation_projects SET name = 'unauthorized' WHERE id = :id"), {"id": other.id})
            await session.commit()
        async with factory() as session:
            assert (await session.get(Project, other.id)).name == "Other", "CROSS_TENANT_WRITE"
        return {"runtime_role": guard, "postgres_rls": "passed", "pool_context_switch": "passed",
                "concurrent_run_start": "passed", "snapshot_immutability": "passed",
                "committed_worker_replay": "passed", "run_id": run_id, "event_id": event_id,
                "database_url": app}
    finally:
        await engine.dispose()
        await owner_engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/acceptance/postgres.json")
    parser.add_argument("--private-context", type=Path)
    args = parser.parse_args()
    try:
        owner, app = prepare(os.environ["EXECUTION_TEST_POSTGRES_URL"], secrets.token_hex(24))
        if sys.platform == "win32":
            with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
                report = runner.run(check(owner, app))
        else:
            report = asyncio.run(check(owner, app))
        if args.private_context:
            args.private_context.parent.mkdir(parents=True, exist_ok=True)
            args.private_context.write_text(json.dumps(report), encoding="utf-8")
        report.pop("database_url")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in report.items() if k not in {"run_id", "event_id"}}))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
