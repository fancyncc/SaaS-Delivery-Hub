import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

from backend.models import Base, ProjectTask, ProjectTaskDocument, ProjectTaskReview
from tests.migration_integrity import (
    assert_foreign_key_enforcement,
    assert_identity_integrity,
    foreign_keys,
    seed_migration_records,
    user_index_ddl,
)


def test_sqlite_task_review_migration(monkeypatch):
    spec = importlib.util.spec_from_file_location('task_review_migration', Path('alembic/versions/0029_task_review_workflow.py'))
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        Base.metadata.create_all(conn)
        seed_migration_records(conn)
        conn.execute(ProjectTask.__table__.insert().values(
            id='task', project_id='project', tenant_id='tenant', title='保留任务', status='done',
            created_by='actor', assignee_id='actor',
        ))
        original_indexes = user_index_ddl(conn)
        original_keys = foreign_keys(conn, 'project_tasks')
        operations = Operations(MigrationContext.configure(conn))
        monkeypatch.setattr(migration, 'op', operations)
        ProjectTaskReview.__table__.drop(conn)
        ProjectTaskDocument.__table__.drop(conn)
        with operations.batch_alter_table('project_tasks', reflect_kwargs={'resolve_fks': False}) as batch:
            batch.drop_column('blocking_reason')
            batch.drop_constraint('ck_project_task_status', type_='check')
            batch.create_check_constraint('ck_project_task_status', "status IN ('todo','in_progress','blocked','done')")
        migration.upgrade()
        assert {'project_task_documents','project_task_reviews'} <= set(inspect(conn).get_table_names())
        assert 'blocking_reason' in {c['name'] for c in inspect(conn).get_columns('project_tasks')}
        assert conn.exec_driver_sql("SELECT title, status, blocking_reason FROM project_tasks WHERE id='task'").one() == ('保留任务', 'in_progress', '')
        assert foreign_keys(conn, 'project_tasks') == original_keys
        assert_identity_integrity(conn, original_indexes)
        with pytest.raises(IntegrityError, match='ck_project_task_status'), conn.begin_nested():
            conn.exec_driver_sql("UPDATE project_tasks SET status='invalid_status' WHERE id='task'")
        conn.exec_driver_sql("UPDATE project_tasks SET status='pending_review' WHERE id='task'")
        migration.downgrade()
        assert 'project_task_reviews' not in inspect(conn).get_table_names()
        assert 'blocking_reason' not in {c['name'] for c in inspect(conn).get_columns('project_tasks')}
        assert conn.exec_driver_sql("SELECT title, status FROM project_tasks WHERE id='task'").one() == ('保留任务', 'in_progress')
        with pytest.raises(IntegrityError, match='ck_project_task_status'), conn.begin_nested():
            conn.exec_driver_sql("UPDATE project_tasks SET status='pending_review' WHERE id='task'")
        assert foreign_keys(conn, 'project_tasks') == original_keys
        assert_identity_integrity(conn, original_indexes)
        migration.upgrade()
        assert foreign_keys(conn, 'project_tasks') == original_keys
        assert_identity_integrity(conn, original_indexes)
    assert_foreign_key_enforcement(engine, [
        ('project_tasks', 'tenant_id', 'task'), ('project_tasks', 'project_id', 'task'),
        ('project_tasks', 'created_by', 'task'), ('project_tasks', 'assignee_id', 'task'),
    ])
    engine.dispose()
