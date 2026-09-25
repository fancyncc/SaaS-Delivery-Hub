"""Verify migration 0029 against PostgreSQL in a rolled-back schema."""
import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def main():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0029_task_review_workflow.py"
    spec = importlib.util.spec_from_file_location("task_review_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(os.environ["MIGRATION_DATABASE_URL"])
    schema = "verify_task_review_" + uuid4().hex
    try:
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                conn.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
                conn.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
                for table in ("users", "customer_tenants", "implementation_projects",
                              "knowledge_documents", "project_tasks"):
                    conn.execute(sa.text(f'CREATE TABLE "{table}" (LIKE public."{table}" INCLUDING ALL)'))
                conn.execute(sa.text('ALTER TABLE project_tasks DROP COLUMN IF EXISTS blocking_reason'))
                conn.execute(sa.text('ALTER TABLE project_tasks DROP CONSTRAINT IF EXISTS ck_project_task_status'))
                conn.execute(sa.text("ALTER TABLE project_tasks ADD CONSTRAINT ck_project_task_status CHECK (status IN ('todo','in_progress','blocked','done'))"))
                migration.op = Operations(MigrationContext.configure(conn))
                migration.upgrade()
                columns = {row["name"] for row in sa.inspect(conn).get_columns("project_tasks")}
                assert "blocking_reason" in columns
                assert {"project_task_documents", "project_task_reviews"} <= set(sa.inspect(conn).get_table_names())
                assert conn.scalar(sa.text("SELECT count(*) FROM pg_policies WHERE schemaname=:schema"),
                                   {"schema": schema}) == 2
                migration.downgrade()
                migration.upgrade()
                print("PostgreSQL: task workflow upgrade, RLS and downgrade/re-upgrade passed.")
            finally:
                transaction.rollback()
                print("Verification transaction rolled back; existing data unchanged.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
