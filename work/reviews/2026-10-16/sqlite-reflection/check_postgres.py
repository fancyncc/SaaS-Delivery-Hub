"""Verify migration compatibility on disposable PostgreSQL, never the live DB."""
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
EVIDENCE = Path(__file__).parent / 'evidence'
name = 'saas-sqlite-fix-check-' + uuid4().hex[:10]
database = 'security_remediation_test_' + uuid4().hex[:10]
password = secrets.token_hex(24)
report = {'scope': 'disposable_postgres', 'status': 'running'}


def docker(*args):
    result = subprocess.run(['docker', *args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('DISPOSABLE_POSTGRES_COMMAND_FAILED')
    return result.stdout.strip()


try:
    docker('run', '-d', '--name', name, '-p', '127.0.0.1::5432',
           '-e', 'POSTGRES_USER=saas', '-e', 'POSTGRES_PASSWORD=' + password,
           '-e', 'POSTGRES_DB=' + database, 'pgvector/pgvector:pg16')
    deadline = time.monotonic() + 60
    while subprocess.run(['docker', 'exec', name, 'pg_isready', '-U', 'saas'], capture_output=True).returncode:
        if time.monotonic() >= deadline:
            raise RuntimeError('DISPOSABLE_POSTGRES_NOT_READY')
        time.sleep(.5)
    port = json.loads(docker('inspect', name))[0]['NetworkSettings']['Ports']['5432/tcp'][0]['HostPort']
    url = f'postgresql+psycopg://saas:{password}@127.0.0.1:{port}/{database}'
    env = {**os.environ, 'DATABASE_URL': url, 'SAAS_ENV_FILE': str(ROOT / 'config/rag.mock.env.example'),
           'PYTHONUTF8': '1', 'PYTHONUNBUFFERED': '1'}

    def migrate(*args):
        with (EVIDENCE / 'postgres-migrations.log').open('ab') as stream:
            result = subprocess.run([sys.executable, '-c',
                "import runpy, sys, warnings; from sqlalchemy.exc import SAWarning; "
                "warnings.simplefilter('error', SAWarning); sys.argv=['alembic', *sys.argv[1:]]; "
                "runpy.run_module('alembic', run_name='__main__')", *args],
                cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=120)
        if result.returncode:
            raise RuntimeError('POSTGRES_MIGRATION_FAILED')

    from sqlalchemy import create_engine, select
    from sqlalchemy.exc import IntegrityError

    from backend.models import KnowledgeDocument, User
    from tests.migration_integrity import seed_migration_records

    migrate('upgrade', 'head')
    engine = create_engine(url)
    query = "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname='public' AND tablename='users'"
    with engine.begin() as connection:
        seed_migration_records(connection)
        original_indexes = dict(connection.exec_driver_sql(query).all())

    def verify():
        with engine.begin() as connection:
            assert dict(connection.exec_driver_sql(query).all()) == original_indexes
            assert {'uq_users_email_normalized', 'uq_users_username_normalized'} <= original_indexes.keys()
            assert connection.scalar(select(KnowledgeDocument.body).where(KnowledgeDocument.id == 'document')) == '迁移前的原始内容'
            for column, value, index in (
                ('username', '  ACTOR  ', 'uq_users_username_normalized'),
                ('email', '  ACTOR@EXAMPLE.TEST  ', 'uq_users_email_normalized'),
            ):
                try:
                    with connection.begin_nested():
                        connection.execute(User.__table__.update().where(User.id == 'optional-1').values(**{column: value}))
                except IntegrityError as error:
                    assert index in str(error)
                else:
                    raise AssertionError('Database accepted a normalized duplicate')

    verify()
    for args in (('downgrade', '0027_scoped_memory'), ('upgrade', 'head')):
        migrate(*args)
        verify()
    engine.dispose()
    report['migration_roundtrip'] = 'passed'
    report['unique_indexes_and_data'] = 'passed'
    env['SECURITY_TEST_POSTGRES_URL'] = url
    with (EVIDENCE / 'postgres-security.log').open('wb') as stream:
        result = subprocess.run([sys.executable, str(ROOT / 'tests/check_security_postgres.py')],
                                cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=180)
    if result.returncode:
        raise RuntimeError('POSTGRES_SECURITY_CHECK_FAILED')
    report['security_integration'] = 'passed'
    report['status'] = 'passed'
except Exception as error:
    report['status'] = 'failed'
    report['error'] = type(error).__name__
    # Avoid including connection URLs or generated credentials in diagnostics.
finally:
    cleanup = subprocess.run(['docker', 'rm', '-f', '-v', name], capture_output=True)
    report['temporary_container_removed'] = cleanup.returncode == 0
    (EVIDENCE / 'postgres.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report))
raise SystemExit(0 if report['status'] == 'passed' else 1)
