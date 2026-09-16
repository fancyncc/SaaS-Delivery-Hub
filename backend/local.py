"""One-command local startup with the project's Python and bundled web UI."""
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    web = root / "frontend"
    entry = web / "dist" / "index.html"
    sources = [web / "package.json", *web.glob("vite.config.*"), *web.glob("index.html"),
               *[p for p in (web / "src").rglob("*") if p.is_file()]]
    if not entry.exists() or any(p.stat().st_mtime > entry.stat().st_mtime for p in sources):
        node = shutil.which("node")
        bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
        if not node and bundled.is_file():
            node = str(bundled)
        if not node or not (web / "node_modules/vite/bin/vite.js").is_file():
            raise SystemExit("Frontend build requires Node.js and frontend/node_modules. Install dependencies first.")
        subprocess.run([node, "node_modules/vite/bin/vite.js", "build"], cwd=web, check=True)

    from backend.config import get_settings
    settings = get_settings()
    url = make_url(settings.migration_database_url or settings.database_url)
    if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
        database = Path(url.database).resolve()
        if database.is_file() and database.stat().st_size:
            backup_dir = root / "backups"
            backup_dir.mkdir(exist_ok=True)
            backup = backup_dir / f"{database.stem}-{datetime.now(UTC):%Y%m%d-%H%M%S-%f}.sqlite3"
            with sqlite3.connect(str(database)) as source, sqlite3.connect(str(backup)) as target:
                source.backup(target)
            print(f"Database backup: {backup}", flush=True)
    migration_env = os.environ.copy()
    if settings.migration_database_url:
        migration_env["DATABASE_URL"] = settings.migration_database_url
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, env=migration_env)
    os.environ["FRONTEND_BASE_URL"] = "http://127.0.0.1:8000"
    print("Open http://127.0.0.1:8000 - application and API share this address.", flush=True)
    subprocess.run([sys.executable, "-m", "backend.serve"], check=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
