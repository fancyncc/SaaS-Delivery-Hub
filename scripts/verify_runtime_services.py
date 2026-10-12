"""Bounded real-service acceptance. No email is sent; no business records change.

--exercise permits a tiny generation/retrieval request, a temporary S3 roundtrip,
and an expiring Worker canary. Reports never contain endpoints or credentials.
This technical report cannot certify production reliability or answer quality.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import smtplib
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from backend.config import get_settings


class ProbeReply(BaseModel):
    status: str


def result(status: str, **detail) -> dict:
    return {"status": status, **detail}


async def capture(check):
    try:
        async with asyncio.timeout(45):
            return await check()
    except Exception as exc:
        detail = getattr(exc, "detail", None)
        codes = {"模型服务认证失败": "MODEL_AUTHENTICATION_FAILED",
                 "模型服务暂不可用": "MODEL_UNAVAILABLE",
                 "模型输出未通过结构化校验，需要人工处理": "MODEL_INVALID_OUTPUT",
                 "模型调用未完成": "MODEL_CALL_INCOMPLETE"}
        reason = codes.get(detail) if isinstance(detail, str) else None
        return result("failed", error_type=type(exc).__name__, **({"reason": reason} if reason else {}))


async def database():
    settings = get_settings()
    if not settings.database_url.startswith("postgresql"):
        return result("not_configured", reason="postgresql_required")
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from backend.runtime_guard import check_runtime_role
    engine = create_async_engine(settings.database_url, poolclass=NullPool, connect_args={"connect_timeout": 3})
    try:
        async with engine.connect() as connection:
            role = await check_runtime_role(connection)
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            head = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()
            if revision != head:
                return result("failed", reason="schema_mismatch")
            return result("passed", schema_revision=revision, runtime_role=role)
    finally:
        await engine.dispose()


async def redis():
    from redis.asyncio import Redis
    async with Redis.from_url(get_settings().redis_url, socket_connect_timeout=3, socket_timeout=3) as client:
        await client.ping()
    return result("passed")


async def smtp_connection():
    settings = get_settings()
    if settings.mail_debug:
        return result("not_configured", reason="mail_debug_enabled")
    def check():
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=5) as client:
            if settings.smtp_starttls:
                client.starttls()
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password)
            if client.noop()[0] != 250:
                raise ValueError("SMTP_NOOP_FAILED")
    await asyncio.to_thread(check)
    return result("passed", scope="connection_authentication_only", email_delivery_verified=False)


async def search():
    settings = get_settings()
    if not settings.opensearch_url:
        return result("not_configured")
    auth = (settings.opensearch_username, settings.opensearch_password) if settings.opensearch_username else None
    async with httpx.AsyncClient(timeout=5, trust_env=False, auth=auth) as client:
        response = await client.get(settings.opensearch_url.rstrip("/") + "/_cluster/health")
        response.raise_for_status()
        healthy = response.json().get("status") in {"green", "yellow"}
    return result("passed" if healthy else "failed", scope="cluster_health_only")


async def generation():
    if get_settings().model_mode == "deterministic":
        return result("not_configured", reason="deterministic_model")
    from backend.intelligence import structured
    reply = await structured("服务验收探测：只返回 status 为 ok 的 JSON", {"expected": "ok"}, ProbeReply)
    return result("passed" if reply.status == "ok" else "failed", scope="structured_protocol_only",
                  answer_quality_verified=False)


async def retrieval():
    settings = get_settings()
    if settings.rag_mode != "real":
        return result("not_configured", reason="mock_retrieval")
    from backend.retrieval_models import embeddings, rank
    vectors = await embeddings(["上线前需完成员工培训。"], query=True)
    hits = await rank("上线前做什么？", [{"id": "acceptance", "text": "上线前需完成员工培训。"}], with_scores=True)
    if len(vectors) != 1 or len(vectors[0]) != settings.embedding_dimensions or len(hits) != 1:
        return result("failed", reason="retrieval_protocol_mismatch")
    return result("passed", dimensions=len(vectors[0]), scope="embedding_rerank_protocol_only",
                  retrieval_quality_verified=False)


async def storage():
    if get_settings().storage_backend != "s3":
        return result("not_configured", reason="database_storage")
    from backend.storage import client
    def roundtrip():
        api = client()
        key = f"__acceptance__/{uuid.uuid4().hex}"
        body = b"saas-runtime-acceptance"
        bucket = get_settings().s3_bucket
        written = False
        try:
            api.put_object(Bucket=bucket, Key=key, Body=body)
            written = True
            response = api.get_object(Bucket=bucket, Key=key)
            with response["Body"] as stream:
                received = stream.read()
            if hashlib.sha256(received).digest() != hashlib.sha256(body).digest():
                raise ValueError("STORAGE_CHECKSUM_MISMATCH")
        finally:
            # Limit cleanup to the unique canary; never list/delete business objects.
            if written:
                api.delete_object(Bucket=bucket, Key=key)
    await asyncio.to_thread(roundtrip)
    return result("passed", scope="temporary_object_roundtrip")


async def worker():
    from redis.asyncio import Redis

    from backend.observability import span, trace_carrier
    from backend.worker import celery
    identifier = uuid.uuid4().hex
    key = f"acceptance:worker:{identifier}"
    async with Redis.from_url(get_settings().redis_url, socket_connect_timeout=3, socket_timeout=3) as client:
        with span("acceptance.worker"):
            # Celery publish is synchronous; keep it outside the asyncio event loop.
            carrier = trace_carrier()
            await asyncio.to_thread(celery.send_task, "implementation.health_probe", args=[identifier], headers=carrier)
            try:
                async with asyncio.timeout(20):
                    while await client.get(key) != b"consumed":
                        await asyncio.sleep(0.25)
            finally:
                await client.delete(key)
    return result("passed", scope="real_queue_consumption_only", crash_recovery_verified=False)


async def run(*, exercise: bool = False) -> dict:
    checks = {}
    for name, check in (("database_runtime", database), ("redis", redis),
                        ("smtp_connection", smtp_connection), ("opensearch", search)):
        checks[name] = await capture(check)
    for name, check in (("generation", generation), ("retrieval", retrieval),
                        ("object_storage", storage), ("worker_canary", worker)):
        checks[name] = await capture(check) if exercise else result("not_run", reason="exercise_flag_required")
    return {"created_at": datetime.now(UTC).isoformat(), "checks": checks,
            "technical_checks_passed": all(c["status"] == "passed" for c in checks.values()),
            "production_acceptance": False,
            "remaining": ["independent_answer_quality", "smtp_delivery", "tenant_concurrency",
                          "real_worker_kill", "business_backup_restore", "alert_delivery"]}


def main():
    from backend.evaluation_artifacts import digest, implementation_checksums, write_once

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exercise", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "data/acceptance" / uuid.uuid4().hex / "runtime_services.json")
    parser.add_argument("--freeze", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; choose a new run path")
    before = implementation_checksums()
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            report = runner.run(run(exercise=args.exercise))
    else:
        report = asyncio.run(run(exercise=args.exercise))
    report.update(schema=1, run_id=uuid.uuid4().hex, implementation_sha256=before,
                  scope="real_service_protocol", status="passed" if report["technical_checks_passed"] else "failed")
    if before != implementation_checksums():
        report["status"] = "failed"
    if args.freeze:
        report["frozen_sha256"] = digest(args.freeze.read_bytes().rstrip())
    write_once(args.output, report)
    print(json.dumps({"checks": report["checks"], "production_acceptance": False}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
