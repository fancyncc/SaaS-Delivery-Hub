import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect


def migration():
    spec = importlib.util.spec_from_file_location("context_migration", Path("alembic/versions/0026_chat_context.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sqlite_context_migration_and_legacy_bootstrap(monkeypatch):
    module = migration()
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(connection)))
        module.upgrade()
        assert {"chat_context_tasks", "chat_context_snapshots"} <= set(inspect(connection).get_table_names())
        # Mirrors fresh bootstrap create_all followed by this migration.
        module.upgrade()
        assert "target_version" in {c["name"] for c in inspect(connection).get_columns("chat_context_tasks")}
        module.downgrade()
        assert "chat_context_tasks" not in inspect(connection).get_table_names()
    engine.dispose()


def test_postgres_migration_contains_rls_and_cascade(monkeypatch):
    import io
    module = migration()
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})
    monkeypatch.setattr(module, "op", Operations(context))

    class Inspector:
        def has_table(self, name):
            return False

        def get_indexes(self, name):
            return []

    monkeypatch.setattr(module.sa, "inspect", lambda bind: Inspector())
    module.upgrade()
    sql = output.getvalue()
    assert sql.count("FORCE ROW LEVEL SECURITY") == 2
    assert sql.count("CREATE POLICY tenant_isolation") == 2
    assert sql.count("ON DELETE CASCADE") == 2


def test_maintenance_migration_preserves_rows_and_handles_fresh_bootstrap(monkeypatch):
    from sqlalchemy import text
    spec = importlib.util.spec_from_file_location("maintenance_migration", Path("alembic/versions/0033_context_maintenance.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE chat_context_tasks (conversation_id VARCHAR(36) PRIMARY KEY, status VARCHAR(24))"))
        connection.execute(text("CREATE TABLE chat_history_migrations (conversation_id VARCHAR(36) PRIMARY KEY, turn_count INTEGER)"))
        connection.execute(text("INSERT INTO chat_context_tasks VALUES ('private-chat', 'failed')"))
        connection.execute(text("INSERT INTO chat_history_migrations VALUES ('private-chat', 123)"))
        monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(connection)))
        module.upgrade()
        module.upgrade()
        assert connection.execute(text("SELECT status, queued_at FROM chat_context_tasks")).one() == ("failed", 0)
        assert connection.execute(text("SELECT turn_count, index_status FROM chat_history_migrations")).one() == (123, "pending")
        module.downgrade()
        assert connection.execute(text("SELECT turn_count FROM chat_history_migrations")).scalar_one() == 123
    engine.dispose()
