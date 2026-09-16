import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from backend.agent_models import KnowledgePiece
from backend.models import KnowledgeDocument, Tenant
from backend.vector_type import Vector512


def migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0020_bge_cpu.py"
    spec = importlib.util.spec_from_file_location("bge_cpu_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_preserves_sources_and_citations_but_requeues_documents(monkeypatch):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE knowledge_documents (id TEXT, body TEXT, index_status TEXT, index_attempts INTEGER, index_error TEXT, index_version TEXT)"))
        conn.execute(text("CREATE TABLE knowledge_chunks (id TEXT, embedding JSON)"))
        conn.execute(text("INSERT INTO knowledge_documents VALUES ('doc', 'original source', 'ready', 3, 'old failure', 'old-index')"))
        conn.execute(text("INSERT INTO knowledge_chunks VALUES ('citation-id', '[1, 0]')"))
        module = migration()
        monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(conn)))
        module.upgrade()
        assert tuple(conn.execute(text("SELECT * FROM knowledge_documents")).one()) == (
            "doc", "original source", "pending", 0, "", "")
        assert tuple(conn.execute(text("SELECT * FROM knowledge_chunks")).one()) == ("citation-id", None)
    engine.dispose()


def test_512_column_type_and_old_vector_rejection():
    dialect = postgresql.dialect()
    column = Vector512()
    assert column.compile(dialect=dialect) == "VECTOR(512)"
    with pytest.raises(ValueError, match="512"):
        column.process_bind_param([0.0] * 1024, dialect)


def test_postgres_migration_invalidates_vectors_without_truncation(monkeypatch):
    statements = []
    class Bind:
        dialect = postgresql.dialect()

        def execute(self, statement):
            statements.append(str(statement))

    class Op:
        def get_bind(self):
            return Bind()

        def execute(self, statement):
            statements.append(statement)

    module = migration()
    monkeypatch.setattr(module, "op", Op())
    module.upgrade()
    assert statements[0] == "SET LOCAL row_security = off"
    assert "USING NULL::vector(512)" in statements[2]
    assert "CREATE INDEX" in statements[3]
    assert "index_status='pending'" in statements[4]


def test_full_sqlite_upgrade_with_existing_1024_vectors(tmp_path):
    root = Path(__file__).resolve().parents[1]
    path = tmp_path / "migration.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{path.as_posix()}",
           "SAAS_ENV_FILE": str(root / "config/rag.mock.env.example")}
    def migrate(*args):
        result = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=root,
                                env=env, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
    migrate("upgrade", "0019_chat_archive")
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    with Session(engine) as session:
        tenant = Tenant(name="Migration", slug="migration-test")
        session.add(tenant)
        session.flush()
        doc = KnowledgeDocument(tenant_id=tenant.id, title="Original", version=1,
            module="import", source="test source", license="internal", body="original body",
            index_status="ready", index_version="legacy")
        session.add(doc)
        session.flush()
        chunk = KnowledgePiece(tenant_id=tenant.id, document_id=doc.id, ordinal=0,
            heading="", body=doc.body, lexemes="original", index_version="legacy")
        session.add(chunk)
        session.flush()
        session.execute(text("UPDATE knowledge_chunks SET embedding=:vector"),
                        {"vector": json.dumps([1.0] + [0.0] * 1023)})
        session.commit()
        document_id, chunk_id = doc.id, chunk.id
    engine.dispose()
    migrate("upgrade", "head")
    with Session(engine) as session:
        doc = session.get(KnowledgeDocument, document_id)
        assert doc.body == "original body" and doc.index_status == "pending"
        assert session.get(KnowledgePiece, chunk_id).embedding is None
    engine.dispose()
    migrate("downgrade", "0019_chat_archive")
