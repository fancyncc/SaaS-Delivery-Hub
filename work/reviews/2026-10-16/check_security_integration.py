"""Run security checks on a fresh PostgreSQL container and always remove it."""
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path(__file__).parent / "evidence"
name = "saas-security-regression-" + uuid4().hex[:12]
database = "security_remediation_test_" + uuid4().hex[:12]
password = secrets.token_hex(24)


def docker(*args):
    result = subprocess.run(["docker", *args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("DISPOSABLE_POSTGRES_COMMAND_FAILED")
    return result.stdout.strip()


report = {"scope": "disposable_postgres_security", "production_acceptance": False}
try:
    docker("run", "-d", "--name", name, "-p", "127.0.0.1::5432",
           "-e", "POSTGRES_USER=saas", "-e", "POSTGRES_PASSWORD=" + password,
           "-e", "POSTGRES_DB=" + database, "pgvector/pgvector:pg16")
    deadline = time.monotonic() + 60
    while True:
        ready = subprocess.run(["docker", "exec", name, "pg_isready", "-U", "saas"], capture_output=True)
        if not ready.returncode:
            break
        if time.monotonic() > deadline:
            raise RuntimeError("DISPOSABLE_POSTGRES_NOT_READY")
        time.sleep(.5)
    port = json.loads(docker("inspect", name))[0]["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostPort"]
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1",
           "SECURITY_TEST_POSTGRES_URL": f"postgresql://saas:{password}@127.0.0.1:{port}/{database}"}
    with (EVIDENCE / "security-postgres.log").open("wb") as stream:
        reply = subprocess.run([sys.executable, str(ROOT / "tests/check_security_postgres.py")],
                               cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT)
    report["status"] = "passed" if not reply.returncode else "failed"
    report["exit_code"] = reply.returncode
finally:
    cleanup = subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True)
    report["temporary_container_removed"] = cleanup.returncode == 0
    (EVIDENCE / "security-postgres.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report))
raise SystemExit(0 if report.get("status") == "passed" else 1)
