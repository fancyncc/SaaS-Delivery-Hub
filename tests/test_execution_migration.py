import os
import subprocess
import sys

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from backend.models import KnowledgeDocument, Tenant


def test_empty_and_previous_head_migrate_and_unused_downgrade(tmp_path):
    database = tmp_path / "migration.db"
    environment = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{database.as_posix()}",
                   "SAAS_ENV_FILE": str(tmp_path / "no-env-file")}
    def migrate(*args):
        result = subprocess.run([sys.executable, "-m", "alembic", *args], env=environment,
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    migrate("upgrade", "0029_task_review_workflow")
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    assert "run_snapshots" not in inspect(engine).get_table_names()
    migrate("upgrade", "0031_conversation_tools")
    with Session(engine) as session:
        tenant = Tenant(name="迁移测试公司", slug="migration-test")
        session.add(tenant)
        session.flush()
        document = KnowledgeDocument(tenant_id=tenant.id, title="保留原始资料", version=1,
            module="测试", source="source.md", license="测试", body="原始资料必须保留",
            index_status="v3_ready")
        session.add(document)
        session.flush()
        session.execute(text("INSERT INTO rag_v3_documents "
            "(id, tenant_id, origin_id, filename, raw, options, phase, error, warnings, digest, identity, attempts, metrics) "
            "VALUES ('stored-source', :tenant, :origin, 'source.md', :raw, '{}', 'ready', '', '[]', 'digest', 'identity', 0, '{}')"),
            {"tenant": tenant.id, "origin": document.id, "raw": b"immutable original bytes"})
        session.commit()
    migrate("upgrade", "head")
    expected_head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == expected_head
        assert connection.scalar(text("SELECT raw FROM retrieval_documents WHERE id='stored-source'")) == b"immutable original bytes"
        assert connection.scalar(text("SELECT index_status FROM knowledge_documents")) == "indexed_ready"
        assert not connection.execute(text("PRAGMA foreign_key_check")).all()
    assert {"tool_state", "tool_state_version"} <= {c["name"] for c in inspect(engine).get_columns("chat_conversations")}
    assert "run_snapshots" in inspect(engine).get_table_names()
    migrate("check")
    migrate("downgrade", "0031_conversation_tools")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT raw FROM rag_v3_documents WHERE id='stored-source'")) == b"immutable original bytes"
        assert connection.scalar(text("SELECT index_status FROM knowledge_documents")) == "v3_ready"
    migrate("upgrade", "head")
    migrate("check")
    migrate("downgrade", "0030_execution_safety")
    assert "tool_state" not in {c["name"] for c in inspect(engine).get_columns("chat_conversations")}
    migrate("downgrade", "0029_task_review_workflow")
    assert "run_snapshots" not in inspect(engine).get_table_names()
    migrate("upgrade", "head")
    engine.dispose()
