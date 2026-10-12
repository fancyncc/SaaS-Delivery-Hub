"""Read-only business manifest for backup/restore, with hashed rows only.

Capture while writers are paused, alongside the dump. Verify only a database
named saas_restore_* or saas_acceptance_*; never restore or overwrite here.
Set RESTORE_VERIFY_DATABASE_URL locally, never pass credentials on the CLI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TABLES = (
    "implementation_projects", "project_documents", "project_memberships", "project_company_collaborations",
    "agent_runs", "agent_steps", "approval_requests", "import_jobs", "workflow_outbox",
    "agent_actions", "agent_memories", "agent_lessons", "idempotency_records",
    "run_snapshots", "saas_workspaces", "saas_members", "tool_executions", "project_artifacts",
    "project_tasks", "project_task_documents", "project_task_reviews",
    "customer_tenants", "users", "tenant_memberships", "role_definitions", "permission_definitions",
    "platform_role_bindings", "project_capability_grants", "support_access_grants",
    "project_feedback", "remediation_tasks", "mail_deliveries", "audit_events",
)


def manifest(raw_url: str, *, verify_target: bool) -> dict:
    url = make_url(raw_url)
    if not url.drivername.startswith("postgresql"):
        raise ValueError("PostgreSQL is required")
    if verify_target and not (url.database or "").startswith(("saas_restore_", "saas_acceptance_")):
        raise ValueError("Use an isolated saas_restore_* database")
    raw = url.set(drivername="postgresql").render_as_string(hide_password=False)
    tables = {}
    with psycopg.connect(raw, connect_timeout=5) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        # Fail instead of silently hashing an RLS-filtered subset.
        connection.execute("SET LOCAL row_security = off")
        # Database/session defaults may differ after restoring to a new database.
        connection.execute("SET LOCAL TIME ZONE 'UTC'")
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        for table in TABLES:
            digest = hashlib.sha256()
            count = 0
            order = "code" if table in {"role_definitions", "permission_definitions"} else "id"
            query = sql.SQL("SELECT to_jsonb(t)::text FROM {} t ORDER BY {}").format(sql.Identifier(table), sql.Identifier(order))
            with connection.cursor(name=f"manifest_{table}") as cursor:
                cursor.execute(query)
                for (row,) in cursor:
                    # Canonical JSON avoids differences in jsonb rendering/key order.
                    value = json.dumps(json.loads(row), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    digest.update(value.encode())
                    digest.update(b"\n")
                    count += 1
            tables[table] = {"count": count, "sha256": digest.hexdigest()}
        # Join tables have composite keys rather than an id column.
        for table, columns in (("role_permissions", ["role_code", "permission_code"]),
                               ("support_access_grant_permissions", ["grant_id", "permission_code"])):
            digest = hashlib.sha256()
            count = 0
            query = sql.SQL("SELECT to_jsonb(t)::text FROM {} t ORDER BY {}").format(
                sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, columns)))
            for (row,) in connection.execute(query):
                digest.update(json.dumps(json.loads(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
                digest.update(b"\n")
                count += 1
            tables[table] = {"count": count, "sha256": digest.hexdigest()}
    return {"schema_revision": revision, "tables": tables}


def compare(expected: dict, actual: dict) -> dict:
    mismatches = []
    if expected.get("schema_revision") != actual.get("schema_revision"):
        mismatches.append("schema_revision")
    expected_tables = expected.get("tables", {})
    actual_tables = actual.get("tables", {})
    required = set(TABLES) | {"role_permissions", "support_access_grant_permissions"}
    if set(expected_tables) != required or set(actual_tables) != required:
        mismatches.append("manifest_coverage")
    for table in sorted(required):
        if expected_tables.get(table) != actual_tables.get(table):
            mismatches.append(table)
    return {"database_business_verified": not mismatches, "mismatches": mismatches,
            "object_storage_verified": False, "rpo_rto_verified": False,
            "production_acceptance": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path)
    parser.add_argument("--expected", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "data/acceptance/restore.json")
    args = parser.parse_args()
    if bool(args.capture) == bool(args.expected):
        parser.error("Choose exactly one of --capture or --expected")
    try:
        actual = manifest(os.environ["RESTORE_VERIFY_DATABASE_URL"], verify_target=bool(args.expected))
        if args.capture:
            args.capture.parent.mkdir(parents=True, exist_ok=True)
            args.capture.write_text(json.dumps(actual, indent=2), encoding="utf-8")
            print("Captured a read-only business manifest; no customer content exported")
            return 0
        report = compare(json.loads(args.expected.read_text(encoding="utf-8")), actual)
        report["created_at"] = datetime.now(UTC).isoformat()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
        return 0 if report["database_business_verified"] else 1
    except Exception as exc:
        # psycopg error messages can contain connection secrets or SQL values.
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
