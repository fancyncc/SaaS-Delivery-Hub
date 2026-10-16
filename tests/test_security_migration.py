import os
import subprocess
import sys


def test_security_migration_fresh_upgrade_and_roundtrip(tmp_path):
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{(tmp_path / 'migration.db').as_posix()}"}
    for command in (("upgrade", "head"), ("downgrade", "0033_context_maintenance"), ("upgrade", "head")):
        result = subprocess.run([sys.executable, "-m", "alembic", *command], env=env,
                                capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr
