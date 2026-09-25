import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, select

from backend.models import Base, PermissionDefinition, ProjectTask, RoleDefinition


def test_existing_database_upgrade_and_downgrade(monkeypatch):
    spec = importlib.util.spec_from_file_location('collaboration_migration', Path('alembic/versions/0028_project_collaboration.py'))
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        Base.metadata.create_all(conn)
        operations = Operations(MigrationContext.configure(conn))
        monkeypatch.setattr(migration, 'op', operations)
        ProjectTask.__table__.drop(conn)
        with operations.batch_alter_table('knowledge_documents') as batch:
            batch.drop_column('submitted_by')
        for role in migration.ROLES:
            conn.execute(RoleDefinition.__table__.insert().values(code=role, scope='project', name=role))
        migration.upgrade()
        assert 'project_tasks' in inspect(conn).get_table_names()
        assert 'submitted_by' in {c['name'] for c in inspect(conn).get_columns('knowledge_documents')}
        assert set(conn.scalars(select(PermissionDefinition.code))) == set(migration.PERMISSIONS)
        migration.downgrade()
        assert 'project_tasks' not in inspect(conn).get_table_names()
        assert 'submitted_by' not in {c['name'] for c in inspect(conn).get_columns('knowledge_documents')}
        migration.upgrade()
    engine.dispose()
