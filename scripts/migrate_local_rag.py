"""Copy a SQLite snapshot to a fresh migrated PostgreSQL database. Never delete source data.

Run with SAAS_ENV_FILE pointing at the private PostgreSQL owner configuration.
Existing business data in the target causes an abort. Derived indexes are rebuilt.
"""
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.dialects.postgresql import insert

from backend import agent_models, chat_models  # noqa: F401
from backend.chat_models import ChatConversation
from backend.config import get_settings
from backend.models import Base, Project, User


def main(source_path):
    root = Path(__file__).resolve().parents[1]
    source_path = Path(source_path).resolve(strict=True)
    backup = root / "backups" / f"rag-migration-{datetime.now(UTC):%Y%m%d-%H%M%S}.sqlite3"
    backup.parent.mkdir(exist_ok=True)
    with sqlite3.connect(source_path) as source, sqlite3.connect(backup) as destination:
        source.backup(destination)
    target = create_engine(get_settings().database_url)
    if target.dialect.name != "postgresql":
        raise ValueError("Target must be PostgreSQL")
    source = create_engine("sqlite:///" + backup.as_posix())
    report = {}
    with source.connect() as src, target.begin() as dst:
        for model in (User, Project, ChatConversation):
            table = model.__table__
            if dst.scalar(select(func.count()).select_from(table)):
                raise ValueError("Target contains business data; refusing to merge")
        available = set(inspect(src).get_table_names())
        # Fresh migration seeds use generated IDs. Replace only these catalog
        # rows in the verified empty target, preserving the source permissions.
        for name in ("role_permissions", "role_definitions", "permission_definitions"):
            dst.execute(Base.metadata.tables[name].delete())
        for table in Base.metadata.sorted_tables:
            if table.name not in available or table.name == "knowledge_chunks":
                continue
            rows = [dict(row) for row in src.execute(select(table)).mappings()]
            for offset in range(0, len(rows), 100):
                dst.execute(insert(table).values(rows[offset:offset + 100]).on_conflict_do_nothing())
            # Every source primary key must survive, including migration-owned seeds.
            keys = list(table.primary_key.columns)
            source_keys = {tuple(row[column.name] for column in keys) for row in rows}
            target_keys = {tuple(row) for row in dst.execute(select(*keys))}
            if not source_keys <= target_keys:
                raise ValueError(f"Primary-key verification failed: {table.name}")
            report[table.name] = len(rows)
    print(json.dumps({"backup": str(backup), "copied_rows": sum(report.values()), "tables": report}, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1])
