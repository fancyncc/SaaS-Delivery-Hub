import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect

from backend.models import Base, ProjectTaskDocument, ProjectTaskReview


def test_sqlite_task_review_migration(monkeypatch):
    spec = importlib.util.spec_from_file_location('task_review_migration', Path('alembic/versions/0029_task_review_workflow.py'))
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        Base.metadata.create_all(conn)
        operations = Operations(MigrationContext.configure(conn))
        monkeypatch.setattr(migration, 'op', operations)
        ProjectTaskReview.__table__.drop(conn)
        ProjectTaskDocument.__table__.drop(conn)
        with operations.batch_alter_table('project_tasks') as batch:
            batch.drop_column('blocking_reason')
            batch.drop_constraint('ck_project_task_status', type_='check')
            batch.create_check_constraint('ck_project_task_status', "status IN ('todo','in_progress','blocked','done')")
        migration.upgrade()
        assert {'project_task_documents','project_task_reviews'} <= set(inspect(conn).get_table_names())
        assert 'blocking_reason' in {c['name'] for c in inspect(conn).get_columns('project_tasks')}
        migration.downgrade()
        assert 'project_task_reviews' not in inspect(conn).get_table_names()
    engine.dispose()
