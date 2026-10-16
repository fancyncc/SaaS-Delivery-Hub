"""Real PostgreSQL/queue/S3/SMTP checks, SIGKILL recovery and backup/restore.

Creates randomly named Docker containers/network and disposable databases.
Uses the cached saas_agent-api image with current source mounted read-only.
Never starts or changes the normal application stack; always cleans its containers.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
OUTPUT = ROOT / "data/acceptance"


def docker(*args, **kwargs):
    reply = subprocess.run(["docker", *args], capture_output=True, **kwargs)
    if reply.returncode:
        raise RuntimeError("DOCKER_COMMAND_FAILED")
    return reply.stdout.decode().strip()


def port(container: str, number: int) -> int:
    info = json.loads(docker("inspect", container))[0]
    return int(info["NetworkSettings"]["Ports"][f"{number}/tcp"][0]["HostPort"])


def wait_pg(container: str):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        reply = subprocess.run(["docker", "exec", container, "pg_isready", "-U", "saas"], capture_output=True)
        if reply.returncode == 0:
            return
        time.sleep(0.5)
    raise TimeoutError("POSTGRES_NOT_READY")


async def recovery(context: dict, *, redis_url: str, worker_name: str) -> dict:
    import httpx
    import psycopg
    from httpx import ASGITransport, AsyncClient
    from redis.asyncio import Redis
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from backend.agent_models import AgentAction
    from backend.db import engine
    from backend.main import app
    from backend.models import AgentRun, AgentStep, User, WorkflowOutbox
    from backend.worker import celery
    from scripts import verify_runtime_services as services

    owner_engine = create_async_engine(context["owner_url"])
    factory = async_sessionmaker(owner_engine, expire_on_commit=False)
    token = context["kill_token"]
    trace_ids = []
    recovered = {}
    async def verify_trace(trace_id):
        from backend.observability import setup
        setup().force_flush(timeout_millis=5000)
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            async with asyncio.timeout(25):
                while True:
                    response = await client.get(context["tempo_api"] + f"/api/traces/{trace_id}")
                    diagnostic = {"http_status": response.status_code,
                        "api_present": "saas-implementation.api" in response.text,
                        "worker_present": "saas-implementation.worker" in response.text}
                    (OUTPUT / "trace-recovery-diagnostic.json").write_text(json.dumps(diagnostic), encoding="utf-8")
                    if response.status_code == 200 and diagnostic["api_present"] and diagnostic["worker_present"]:
                        return
                    await asyncio.sleep(0.5)
    try:
        # Read synthetic acceptance login from the owner context; no customer data.
        async with factory() as session:
            user = await session.scalar(select(User).where(User.email.like("accept-0-%@example.test")))
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            logged = await client.post("/api/auth/login", json={"email": user.email, "password": "AcceptanceOnly123!"})
            assert logged.status_code == 200
            client.headers["X-CSRF-Token"] = client.cookies.get("saas_csrf")
            from test_api import project_body
            created = await client.post("/api/projects", headers={"Idempotency-Key": uuid.uuid4().hex}, json=project_body())
            assert created.status_code == 200
            p = created.json()["data"]
            started = await client.post(f"/api/projects/{p['id']}/runs", headers={"Idempotency-Key": uuid.uuid4().hex})
            assert started.status_code == 200
            run_id = started.json()["data"]["id"]
            trace_ids.append(started.json()["trace_id"])
            async with factory() as session:
                event = await session.scalar(select(WorkflowOutbox).where(WorkflowOutbox.run_id == run_id))
                event_id = event.id
            await asyncio.to_thread(celery.send_task, "implementation.run", args=[event_id])
            async with Redis.from_url(redis_url, socket_timeout=3, socket_connect_timeout=3) as redis:
                async with asyncio.timeout(45):
                    while not await redis.get(f"acceptance:kill-gate:{token}:{run_id}"):
                        await asyncio.sleep(0.25)
            async with factory() as session:
                before = list(await session.scalars(select(AgentStep).where(AgentStep.run_id == run_id)))
                assert len(before) == 2
                assert any(s.node == "create_project" for s in before)
                committed_ids = {s.id for s in before}
            await asyncio.to_thread(docker, "kill", "--signal=KILL", worker_name)
            await asyncio.to_thread(docker, "start", worker_name)
            # Republish the durable outbox; do not depend on the broker visibility timeout.
            await asyncio.to_thread(celery.send_task, "implementation.run", args=[event_id])
            async with asyncio.timeout(60):
                while True:
                    async with factory() as session:
                        run = await session.get(AgentRun, run_id)
                        actions = list(await session.scalars(select(AgentAction).where(AgentAction.run_id == run_id)))
                        event = await session.get(WorkflowOutbox, event_id)
                        diagnostic = {"status": run.status,
                            "current_node": run.current_node, "event_error_type": event.last_error,
                            "actions": [{"tool": a.tool, "status": a.status,
                                         "error_code": a.result.get("error_code")} for a in actions]}
                        (OUTPUT / "worker-recovery-diagnostic.json").write_text(json.dumps(diagnostic, indent=2), encoding="utf-8")
                        if run.status in {"blocked", "failed", "cancelled"}:
                            raise AssertionError("WORKER_RECOVERY_TERMINATED")
                        if run.status == "waiting_approval":
                            steps = list(await session.scalars(select(AgentStep).where(AgentStep.run_id == run_id)))
                            assert len(steps) == 11
                            assert committed_ids <= {s.id for s in steps}
                            assert len({s.sequence for s in steps}) == len(steps)
                            assert len(actions) == 5
                            assert all(a.status == "succeeded" for a in actions)
                            recovered = {"status": "passed", "steps": len(steps), "actions": len(actions)}
                            print("Agent Worker SIGKILL recovery passed", flush=True)
                            break
                    await asyncio.sleep(0.25)
            # Let the recovered Worker's completed spans reach Tempo before
            # recovery validation completes with both API and Worker spans present.
            await verify_trace(trace_ids[-1])
        report = {}
        for name, check in (("database_runtime", services.database), ("redis", services.redis),
                            ("smtp_connection", services.smtp_connection), ("object_storage", services.storage),
                            ("worker_canary", services.worker)):
            report[name] = await services.capture(check)
        # Persist sanitized per-service results before asserting; the outer error
        # report deliberately omits arbitrary exception text and credentials.
        (OUTPUT / "real-service-probes.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        assert all(item["status"] == "passed" for item in report.values()), "REAL_SERVICE_CHECK_FAILED"
        print("Real queue, SMTP, object storage and trace probes passed; verifying restore", flush=True)
        # Exercise privilege inheritance guard on this disposable PostgreSQL role.
        from backend.runtime_guard import UnsafeDatabaseRole, check_runtime_role
        owner_raw = context["owner_url"].replace("postgresql+psycopg://", "postgresql://", 1)
        with psycopg.connect(owner_raw, autocommit=True) as connection:
            connection.execute("CREATE ROLE acceptance_unsafe_parent BYPASSRLS")
            connection.execute("GRANT acceptance_unsafe_parent TO execution_acceptance_app")
        try:
            async with engine.connect() as connection:
                try:
                    await check_runtime_role(connection)
                except UnsafeDatabaseRole:
                    pass
                else:
                    raise AssertionError("INHERITED_PRIVILEGE_NOT_REJECTED")
        finally:
            with psycopg.connect(owner_raw, autocommit=True) as connection:
                connection.execute("REVOKE acceptance_unsafe_parent FROM execution_acceptance_app")
                connection.execute("DROP ROLE acceptance_unsafe_parent")
        # Send only a synthetic message to this run's isolated Mailpit container.
        from backend.mailer import smtp_send
        from backend.models import MailDelivery
        await asyncio.to_thread(smtp_send, MailDelivery(id=uuid.uuid4().hex, email="acceptance@example.test",
            purpose="isolated-acceptance", body="http://acceptance.invalid/synthetic-link"))
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            response = await client.get(context["mail_api"] + "/api/v1/messages")
            response.raise_for_status()
            messages = response.json()["messages"]
            assert any(m["Subject"] == "SaaS Implementation: isolated-acceptance" for m in messages)
        return {"real_services": report, "worker_sigkill_recovery": "passed",
                "worker_recovery": recovered,
                "inherited_privilege_guard": "passed", "isolated_smtp_receipt": "passed",
                "api_worker_trace_storage": "passed"}
    finally:
        await owner_engine.dispose()
        await engine.dispose()


def main():
    global OUTPUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    OUTPUT = args.output_dir or ROOT / "data/acceptance" / uuid.uuid4().hex
    OUTPUT.mkdir(parents=True, exist_ok=False)
    identifier = uuid.uuid4().hex[:12]
    prefix = f"saas-execution-acceptance-{identifier}"
    network = prefix
    names: list[str] = []
    worker_name = prefix + "-worker"
    owner_password, storage_password = secrets.token_hex(24), secrets.token_hex(24)
    database = f"saas_acceptance_{identifier}"
    report = {"production_acceptance": False}
    private = OUTPUT / f"private-{identifier}.json"
    worker_env = OUTPUT / f"worker-{identifier}.env"
    try:
        docker("network", "create", network)
        def launch(service, image, *arguments):
            name = prefix + "-" + service
            docker("run", "-d", "--name", name, "--network", network, *arguments, image,
                   *({"redis": ["redis-server"], "minio": ["server", "/data"],
                      # This isolated sink needs no client reverse-DNS lookup;
                      # Windows/Docker gateway PTR resolution may stall SMTP's
                      # greeting before the bounded connection probe can run.
                      "mail": ["--smtp-disable-rdns"]}.get(service, [])))
            names.append(name)
            return name
        postgres = launch("postgres", "pgvector/pgvector:pg16", "-p", "127.0.0.1::5432",
            "-e", f"POSTGRES_PASSWORD={owner_password}", "-e", "POSTGRES_USER=saas", "-e", f"POSTGRES_DB={database}")
        redis_name = launch("redis", "redis:7-alpine", "-p", "127.0.0.1::6379")
        mail = launch("mail", "axllent/mailpit:v1.27", "-p", "127.0.0.1::1025", "-p", "127.0.0.1::8025")
        storage = launch("minio", "quay.io/minio/minio:RELEASE.2025-04-22T22-12-26Z", "-p", "127.0.0.1::9000",
            "-e", "MINIO_ROOT_USER=acceptance", "-e", f"MINIO_ROOT_PASSWORD={storage_password}")
        wait_pg(postgres)
        tempo_name = prefix + "-tempo"
        docker("run", "-d", "--name", tempo_name, "--network", network,
               "--tmpfs", "/var/tempo:uid=10001,gid=10001,mode=0750", "-p", "127.0.0.1::3200",
               "-p", "127.0.0.1::4318", "-v", (ROOT / "scripts/tempo.yml").as_posix() + ":/etc/tempo.yml:ro",
               "grafana/tempo:2.7.1", "-config.file=/etc/tempo.yml")
        names.append(tempo_name)
        owner_url = f"postgresql+psycopg://saas:{owner_password}@127.0.0.1:{port(postgres,5432)}/{database}"
        environment = {**os.environ, "EXECUTION_TEST_POSTGRES_URL": owner_url}
        checked = subprocess.run([sys.executable, str(ROOT / "scripts/check_execution_postgres.py"),
            "--private-context", str(private), "--output", str(OUTPUT / "postgres.json")], env=environment,
            capture_output=True, text=True)
        if checked.returncode:
            (OUTPUT / "postgres-diagnostic.log").write_text(checked.stdout + checked.stderr, encoding="utf-8")
            raise RuntimeError("POSTGRES_ACCEPTANCE_FAILED")
        context = json.loads(private.read_text(encoding="utf-8"))
        context.update({"owner_url": owner_url, "kill_token": uuid.uuid4().hex,
            "mail_api": f"http://127.0.0.1:{port(mail,8025)}", "tempo_api": f"http://127.0.0.1:{port(tempo_name,3200)}"})
        from sqlalchemy.engine import make_url
        app_url = make_url(context["database_url"])
        redis_url = f"redis://127.0.0.1:{port(redis_name,6379)}/0"
        values = {"DATABASE_URL": app_url.set(host=postgres, port=5432).render_as_string(hide_password=False),
            "APP_DB_USER": app_url.username, "SAAS_ENV_FILE": "/nonexistent/acceptance.env",
            "MIGRATION_DATABASE_URL": "", "REDIS_URL": f"redis://{redis_name}:6379/0",
            "MODEL_MODE": "deterministic", "RAG_MODE": "mock", "EXECUTION_MODE": "worker",
            "STORAGE_BACKEND": "s3", "S3_ENDPOINT": f"http://{storage}:9000", "S3_BUCKET": "acceptance",
            "S3_ACCESS_KEY": "acceptance", "S3_SECRET_KEY": storage_password,
            "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://{tempo_name}:4318",
            "MAIL_DEBUG": "true", "ACCEPTANCE_KILL_TOKEN": context["kill_token"]}
        worker_env.write_text("\n".join(f"{key}={value}" for key, value in values.items()), encoding="utf-8")
        docker("run", "-d", "--name", worker_name, "--network", network, "--env-file", str(worker_env),
               "-v", ROOT.as_posix() + ":/app:ro", "saas_agent-api", "python", "scripts/acceptance_worker.py")
        names.append(worker_name)
        os.environ.update({**{k: str(v) for k, v in values.items()}, "DATABASE_URL": context["database_url"],
            "REDIS_URL": redis_url, "SAAS_ENV_FILE": str(OUTPUT / "nonexistent.env"),
            "MAIL_DEBUG": "false", "SMTP_HOST": "127.0.0.1", "SMTP_PORT": str(port(mail,1025)),
            "STORAGE_BACKEND": "s3", "S3_ENDPOINT": f"http://127.0.0.1:{port(storage,9000)}",
            "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{port(tempo_name,4318)}",
            "S3_ACCESS_KEY": "acceptance", "S3_SECRET_KEY": storage_password, "S3_BUCKET": "acceptance"})
        from backend.storage import client
        deadline = time.monotonic() + 30
        while True:
            try:
                client().create_bucket(Bucket="acceptance")
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.5)
        print("Isolated PostgreSQL migration and RLS checks passed; exercising real Worker SIGKILL", flush=True)
        if sys.platform == "win32":
            with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
                report.update(runner.run(recovery(context, redis_url=redis_url, worker_name=worker_name)))
        else:
            report.update(asyncio.run(recovery(context, redis_url=redis_url, worker_name=worker_name)))
        docker("stop", worker_name)
        from scripts.verify_restored_database import compare, manifest
        expected = manifest(owner_url, verify_target=False)
        backup = OUTPUT / f"database-{identifier}.dump"
        with backup.open("wb") as stream:
            subprocess.run(["docker", "exec", postgres, "pg_dump", "-U", "saas", "-d", database, "-Fc"],
                           stdout=stream, stderr=subprocess.PIPE, check=True)
        target = f"saas_restore_{identifier}"
        docker("exec", postgres, "createdb", "-U", "saas", target)
        import psycopg
        from psycopg import sql
        with psycopg.connect(owner_url.replace("postgresql+psycopg://", "postgresql://", 1), autocommit=True) as connection:
            connection.execute(sql.SQL("ALTER DATABASE {} SET timezone = 'Asia/Shanghai'").format(sql.Identifier(target)))
        started = time.monotonic()
        with backup.open("rb") as stream:
            subprocess.run(["docker", "exec", "-i", postgres, "pg_restore", "-U", "saas", "-d", target,
                            "--no-owner", "--exit-on-error"], stdin=stream, capture_output=True, check=True)
        restored_url = make_url(owner_url).set(database=target).render_as_string(hide_password=False)
        restored = compare(expected, manifest(restored_url, verify_target=True))
        assert restored["database_business_verified"], "RESTORE_CONTENT_MISMATCH"
        database_restore_seconds = round(time.monotonic() - started, 3)
        # Recover actual generated artifacts to another isolated bucket and verify bytes.
        from backend.delivery import digest
        api = client()
        recovered_bucket = "acceptance-restored"
        api.create_bucket(Bucket=recovered_bucket)
        artifact_directory = OUTPUT / f"artifact-backup-{identifier}"
        artifact_directory.mkdir()
        artifact_count = 0
        with psycopg.connect(owner_url.replace("postgresql+psycopg://", "postgresql://", 1)) as connection:
            for tenant, project, checksum, content, artifact_run in connection.execute(
                    "SELECT tenant_id, project_id, checksum, content, run_id FROM project_artifacts"):
                key = f"{tenant}/{project}/{checksum}"
                # The preflight fixture used DB storage; explicitly materialize its synthetic artifact.
                try:
                    received = api.get_object(Bucket="acceptance", Key=key)["Body"].read()
                except api.exceptions.NoSuchKey:
                    assert artifact_run == context["run_id"], "WORKER_ARTIFACT_MISSING"
                    received = content.encode()
                    api.put_object(Bucket="acceptance", Key=key, Body=received)
                assert digest(received.decode()) == checksum
                backup_file = artifact_directory / checksum
                backup_file.write_bytes(received)
                archived = backup_file.read_bytes()
                assert digest(archived.decode()) == checksum
                api.put_object(Bucket=recovered_bucket, Key=key, Body=archived)
                recovered = api.get_object(Bucket=recovered_bucket, Key=key)["Body"].read()
                assert digest(recovered.decode()) == checksum
                artifact_count += 1
        assert artifact_count > 0
        restored["object_storage_verified"] = True
        report.update({"database_restore": restored, "database_restore_seconds": database_restore_seconds,
                       "restore_timezone_normalization": "passed",
                       "scope": "synthetic_isolated_data", "real_generation_quality_verified": False,
                       "real_retrieval_quality_verified": False, "smtp_external_delivery_verified": False,
                       "restored_artifact_count": artifact_count})
        (OUTPUT / "isolated_execution.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
        return 0
    except Exception as exc:
        report.update({"status": "failed", "error_type": type(exc).__name__})
        (OUTPUT / "isolated_execution.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        for name in names:
            logs = subprocess.run(["docker", "logs", name], capture_output=True)
            (OUTPUT / f"{name}.log").write_bytes(logs.stdout + logs.stderr)
        print(json.dumps(report))
        return 1
    finally:
        # Every name comes from this run; never enumerate/delete existing services.
        for name in reversed(names):
            subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True)
        subprocess.run(["docker", "network", "rm", network], capture_output=True)
        private.unlink(missing_ok=True)
        worker_env.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
