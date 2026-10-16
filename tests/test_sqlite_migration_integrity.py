"""Exercise the actual Alembic revision chain with SAWarnings as errors."""
import os
import subprocess
import sys

from sqlalchemy import create_engine

from tests.migration_integrity import (
    assert_identity_integrity,
    seed_migration_records,
    user_index_ddl,
)


def test_sqlite_revision_chain_preserves_data_and_unique_indexes(tmp_path):
    database = tmp_path / 'revision-chain.db'
    environment = {
        **os.environ,
        'DATABASE_URL': f'sqlite+aiosqlite:///{database.as_posix()}',
        'SAAS_ENV_FILE': str(tmp_path / 'no-env-file'),
    }

    def migrate(*args):
        # Set the filter in the child interpreter as well as pytest; -W flags
        # passed to pytest alone do not protect migration subprocesses.
        result = subprocess.run([
            sys.executable, '-c',
            "import runpy, sys, warnings; from sqlalchemy.exc import SAWarning; "
            "warnings.simplefilter('error', SAWarning); "
            "sys.argv = ['alembic', *sys.argv[1:]]; runpy.run_module('alembic', run_name='__main__')",
            *args,
        ], env=environment, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr

    migrate('upgrade', 'head')
    engine = create_engine(f'sqlite:///{database.as_posix()}')
    try:
        with engine.begin() as connection:
            seed_migration_records(connection)
            original_indexes = user_index_ddl(connection)
            assert_identity_integrity(connection, original_indexes)
        for command in (('downgrade', '0027_scoped_memory'), ('upgrade', 'head')):
            migrate(*command)
            with engine.begin() as connection:
                assert_identity_integrity(connection, original_indexes)
    finally:
        engine.dispose()
