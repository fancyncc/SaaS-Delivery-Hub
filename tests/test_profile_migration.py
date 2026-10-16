import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from tests.migration_integrity import user_index_ddl


def upgrade(connection):
    spec = importlib.util.spec_from_file_location('profile_migration', Path(__file__).parents[1] / 'alembic/versions/0015_username_profile.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(connection)):
        module.upgrade()


def test_profile_migration_preserves_legacy_and_allows_optional_contacts():
    engine = sa.create_engine('sqlite://')
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, email VARCHAR(160) NOT NULL UNIQUE)')
        connection.exec_driver_sql('CREATE TABLE email_verifications (id VARCHAR(36) PRIMARY KEY)')
        connection.exec_driver_sql("INSERT INTO users VALUES ('old', 'old@example.com')")
        upgrade(connection)
        user = connection.exec_driver_sql('SELECT username, email, phone FROM users').one()
        assert user.username.startswith('u_') and user.email == 'old@example.com' and user.phone is None
        connection.exec_driver_sql("INSERT INTO users(id,username) VALUES ('new1','first'), ('new2','second')")
        assert 'user_id' in {c['name'] for c in sa.inspect(connection).get_columns('email_verifications')}
        with pytest.raises(sa.exc.IntegrityError):
            connection.exec_driver_sql("INSERT INTO users(id,username) VALUES ('duplicate','FIRST')")
    engine.dispose()


def indexed_legacy_schema(connection, populated):
    connection.exec_driver_sql('CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, username VARCHAR(40) UNIQUE, email VARCHAR(160) NOT NULL UNIQUE, display_name TEXT)')
    connection.exec_driver_sql('CREATE TABLE email_verifications (id VARCHAR(36) PRIMARY KEY)')
    connection.exec_driver_sql('CREATE UNIQUE INDEX uq_users_email_normalized ON users (lower(trim(email)))')
    connection.exec_driver_sql('CREATE UNIQUE INDEX uq_users_username_normalized ON users (lower(trim(username)))')
    connection.exec_driver_sql('CREATE UNIQUE INDEX "custom display identity" ON users (lower(trim(display_name))) WHERE display_name IS NOT NULL')
    if populated:
        connection.exec_driver_sql("INSERT INTO users VALUES ('old', 'Legacy', 'old@example.test', 'Original')")


@pytest.mark.parametrize('populated', [False, True])
def test_profile_rebuild_preserves_expression_index_ddl_and_enforcement(populated):
    engine = sa.create_engine('sqlite://')
    with engine.begin() as connection:
        indexed_legacy_schema(connection, populated)
        original = user_index_ddl(connection)
        upgrade(connection)
        assert user_index_ddl(connection) == original
        if populated:
            assert connection.exec_driver_sql("SELECT username, email FROM users WHERE id='old'").one() == ('legacy', 'old@example.test')
            for column, value, index in (
                ('username', '  LEGACY  ', 'uq_users_username_normalized'),
                ('email', '  OLD@EXAMPLE.TEST  ', 'uq_users_email_normalized'),
                ('display_name', '  ORIGINAL  ', 'custom display identity'),
            ):
                values = dict(id='duplicate', username='new', email='new@example.test', display_name=None)
                values[column] = value
                with pytest.raises(sa.exc.IntegrityError, match=index), connection.begin_nested():
                    connection.execute(sa.text('INSERT INTO users (id, username, email, display_name) VALUES (:id, :username, :email, :display_name)'), values)
        connection.exec_driver_sql("INSERT INTO users (id,username) VALUES ('optional-1','optional-1'), ('optional-2','optional-2')")
    engine.dispose()


@pytest.mark.parametrize('populated', [False, True])
@pytest.mark.parametrize('failure_phase', ['replace_table', 'restore_index'])
def test_failed_profile_rebuild_rolls_back_expression_indexes(populated, failure_phase):
    engine = sa.create_engine('sqlite://')
    with engine.begin() as connection:
        indexed_legacy_schema(connection, populated)
        original_indexes = user_index_ddl(connection)
        original_rows = connection.exec_driver_sql('SELECT id, username, email, display_name FROM users').all()

        def fail_table_replacement(conn, cursor, statement, parameters, context, executemany):
            prefix = ('CREATE TABLE _alembic_tmp_users' if failure_phase == 'replace_table'
                      else 'CREATE UNIQUE INDEX "custom display identity"')
            if statement.lstrip().startswith(prefix):
                raise RuntimeError('injected table replacement failure')

        sa.event.listen(connection, 'before_cursor_execute', fail_table_replacement)
        try:
            with pytest.raises(RuntimeError, match='injected table replacement failure'):
                upgrade(connection)
        finally:
            sa.event.remove(connection, 'before_cursor_execute', fail_table_replacement)
        assert user_index_ddl(connection) == original_indexes
        # Username normalization precedes the rebuild and remains part of the
        # surrounding transaction; the original identities must still exist.
        assert connection.exec_driver_sql('SELECT id, lower(username), email, display_name FROM users').all() == [
            (row[0], row[1].lower(), row[2], row[3]) for row in original_rows
        ]
        assert '_alembic_tmp_users' not in sa.inspect(connection).get_table_names()
        if populated:
            with pytest.raises(sa.exc.IntegrityError, match='uq_users_email_normalized'), connection.begin_nested():
                connection.exec_driver_sql("INSERT INTO users (id,username,email) VALUES ('duplicate','different','  OLD@EXAMPLE.TEST  ')")
    engine.dispose()
