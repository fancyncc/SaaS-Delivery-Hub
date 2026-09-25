"""Verify migration 0028 with psycopg in a transaction that always rolls back.

Run inside the migrate container with MIGRATION_DATABASE_URL configured.
The existing public tables are copied as empty structures into an isolated schema.
"""
import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def main():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0028_project_collaboration.py"
    spec = importlib.util.spec_from_file_location("collaboration_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(os.environ["MIGRATION_DATABASE_URL"])
    schema = "verify_collaboration_" + uuid4().hex
    try:
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                conn.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
                conn.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
                for table in ("users", "customer_tenants", "implementation_projects",
                              "knowledge_documents", "role_definitions", "permission_definitions", "role_permissions"):
                    conn.execute(sa.text(f'CREATE TABLE "{table}" (LIKE public."{table}" INCLUDING ALL)'))
                conn.execute(sa.text('ALTER TABLE knowledge_documents DROP COLUMN IF EXISTS submitted_by'))
                conn.execute(sa.text('INSERT INTO role_definitions SELECT * FROM public.role_definitions'))
                migration.op = Operations(MigrationContext.configure(conn))
                migration.upgrade()
                assert conn.scalar(sa.text('SELECT count(*) FROM permission_definitions')) == 2
                assert conn.scalar(sa.text('SELECT count(*) FROM role_permissions')) == 10
                descriptions = conn.execute(sa.text('SELECT code, description FROM permission_definitions')).all()
                assert all(code == description for code, description in descriptions)
                assert conn.scalar(sa.text("SELECT count(*) FROM pg_policies WHERE schemaname = :schema"),
                                   {"schema": schema}) == 2
                migration.downgrade()
                migration.upgrade()
                assert conn.scalar(sa.text('SELECT count(*) FROM role_permissions')) == 10
                print("PostgreSQL/psycopg: upgrade, permissions, RLS policies and downgrade/re-upgrade passed.")
            finally:
                transaction.rollback()
                print("Verification transaction rolled back; existing data unchanged.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
