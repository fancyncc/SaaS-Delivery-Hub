import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, select

from backend.models import Base, PermissionDefinition, ProjectTask, RoleDefinition
from tests.migration_integrity import (
    assert_foreign_key_enforcement,
    assert_identity_integrity,
    foreign_keys,
    seed_migration_records,
    user_index_ddl,
)


def test_existing_database_upgrade_and_downgrade(monkeypatch):
    spec = importlib.util.spec_from_file_location('collaboration_migration', Path('alembic/versions/0028_project_collaboration.py'))
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        Base.metadata.create_all(conn)
        seed_migration_records(conn)
        original_indexes = user_index_ddl(conn)
        original_keys = foreign_keys(conn, 'knowledge_documents') - {(('submitted_by',), 'users', ('id',), ())}
        operations = Operations(MigrationContext.configure(conn))
        monkeypatch.setattr(migration, 'op', operations)
        ProjectTask.__table__.drop(conn)
        with operations.batch_alter_table('knowledge_documents', reflect_kwargs={'resolve_fks': False}) as batch:
            batch.drop_column('submitted_by')
        for role in migration.ROLES:
            conn.execute(RoleDefinition.__table__.insert().values(code=role, scope='project', name=role))
        migration.upgrade()
        assert 'project_tasks' in inspect(conn).get_table_names()
        assert 'submitted_by' in {c['name'] for c in inspect(conn).get_columns('knowledge_documents')}
        assert set(conn.scalars(select(PermissionDefinition.code))) == set(migration.PERMISSIONS)
        assert foreign_keys(conn, 'knowledge_documents') == original_keys | {(('submitted_by',), 'users', ('id',), ())}
        assert_identity_integrity(conn, original_indexes)
        migration.downgrade()
        assert 'project_tasks' not in inspect(conn).get_table_names()
        assert 'submitted_by' not in {c['name'] for c in inspect(conn).get_columns('knowledge_documents')}
        assert foreign_keys(conn, 'knowledge_documents') == original_keys
        assert_identity_integrity(conn, original_indexes)
        migration.upgrade()
        assert_identity_integrity(conn, original_indexes)
    assert_foreign_key_enforcement(engine, [
        ('knowledge_documents', 'tenant_id', 'document'),
        ('knowledge_documents', 'project_id', 'document'),
        ('knowledge_documents', 'submitted_by', 'document'),
    ])
    engine.dispose()
