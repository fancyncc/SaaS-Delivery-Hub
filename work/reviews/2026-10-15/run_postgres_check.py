from pathlib import Path
import os, subprocess, sys, uuid
from dotenv import dotenv_values
import psycopg
from psycopg import sql
from sqlalchemy.engine import URL
v=dotenv_values('.env');name='security_remediation_test_'+uuid.uuid4().hex[:8]
url=URL.create('postgresql',username=v.get('POSTGRES_USER','saas'),password=v.get('SAAS_DB_PASSWORD','saas'),host='127.0.0.1',port=5433,database=v.get('POSTGRES_DB','saas_agent'))
with psycopg.connect(url.render_as_string(hide_password=False),autocommit=True) as c:
 c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
env={**os.environ,'SECURITY_TEST_POSTGRES_URL':url.set(database=name).render_as_string(hide_password=False),'PYTHONUTF8':'1'}
with Path('work/reviews/2026-10-15/evidence/repair-postgres.log').open('w',encoding='utf-8') as log:
 result=subprocess.run([sys.executable,'tests/check_security_postgres.py'],env=env,stdout=log,stderr=subprocess.STDOUT)
print('Isolated PostgreSQL security checks exit:',result.returncode)
print('Disposable database:',name)
with Path('tmp/security-test-databases.txt').open('a') as f:f.write(name+'\n')
raise SystemExit(result.returncode)
