"""Reject privileged application identities before serving or consuming work."""
from __future__ import annotations

from sqlalchemy import text

from backend.config import get_settings

PROTECTED_TABLES = (
    "implementation_projects", "project_documents", "agent_runs", "agent_steps",
    "approval_requests", "import_jobs", "saas_workspaces", "saas_members",
    "tool_executions", "project_artifacts", "project_feedback", "run_snapshots",
    "agent_actions", "agent_memories", "agent_lessons",
)


class UnsafeDatabaseRole(RuntimeError):
    pass


def validate_role(roles: list[dict], tables: list[dict], *, expected_user: str) -> None:
    if not roles or roles[0]["current_user"] != expected_user:
        raise UnsafeDatabaseRole("DATABASE_ROLE_MISMATCH")
    if any(r["rolsuper"] or r["rolbypassrls"] or r["rolcreaterole"] or r["rolcreatedb"] for r in roles):
        raise UnsafeDatabaseRole("DATABASE_ROLE_PRIVILEGED")
    if {t["name"] for t in tables} != set(PROTECTED_TABLES):
        raise UnsafeDatabaseRole("DATABASE_PROTECTED_TABLE_MISSING")
    if any(t["can_own"] or t["can_truncate"] for t in tables):
        raise UnsafeDatabaseRole("DATABASE_ROLE_CAN_BYPASS_TABLE_POLICY")
    if any(not t["rls"] or not t["force_rls"] for t in tables):
        raise UnsafeDatabaseRole("DATABASE_RLS_NOT_ENFORCED")


async def check_runtime_role(connection) -> dict:
    if connection.dialect.name != "postgresql":
        return {"status": "not_applicable", "reason": "sqlite_development"}
    settings = get_settings()
    if settings.migration_database_url:
        raise UnsafeDatabaseRole("DATABASE_MIGRATION_CREDENTIAL_IN_RUNTIME")
    roles = list((await connection.execute(text("""
        SELECT current_user AS current_user, rolname, rolsuper, rolbypassrls,
               rolcreaterole, rolcreatedb
        FROM pg_roles WHERE rolname = current_user OR pg_has_role(current_user, oid, 'MEMBER')
        ORDER BY (rolname = current_user) DESC
    """))).mappings())
    tables = list((await connection.execute(text("""
        SELECT c.relname AS name, c.relrowsecurity AS rls, c.relforcerowsecurity AS force_rls,
               pg_has_role(current_user, c.relowner, 'MEMBER') AS can_own,
               has_table_privilege(current_user, c.oid, 'TRUNCATE') AS can_truncate
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = ANY(:tables)
    """), {"tables": list(PROTECTED_TABLES)})).mappings())
    validate_role([dict(r) for r in roles], [dict(t) for t in tables], expected_user=settings.app_db_user)
    return {"status": "passed", "protected_tables": len(tables)}
