"""Add a Redis secret without changing existing database or account credentials."""
import secrets
from pathlib import Path

path = Path(__file__).resolve().parents[1] / ".env"
existing = path.read_text(encoding="utf-8") if path.exists() else ""
values = dict(line.split("=", 1) for line in existing.splitlines() if "=" in line and not line.startswith("#"))
if not values.get("REDIS_PASSWORD"):
    secret = secrets.token_hex(32)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"\nREDIS_PASSWORD={secret}\n")
        if not values.get("REDIS_URL") or "@" not in values["REDIS_URL"]:
            stream.write(f"REDIS_URL=redis://:{secret}@127.0.0.1:6379/0\n")
    print("Created private Redis configuration; existing account/database secrets preserved.")
else:
    print("Existing Redis secret preserved.")
