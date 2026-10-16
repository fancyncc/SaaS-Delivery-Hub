"""Generate a local-only environment. Never overwrites an existing secret file."""
import secrets
from pathlib import Path

target = Path(__file__).resolve().parent.parent / ".env.preproduction"
if target.exists():
    existing = dict(line.split("=", 1) for line in target.read_text(encoding="utf-8").splitlines() if "=" in line and not line.startswith("#"))
    if not existing.get("REDIS_PASSWORD"):
        redis_password = secrets.token_hex(32)
        with target.open("a", encoding="utf-8") as stream:
            stream.write(f"\nREDIS_PASSWORD={redis_password}\nREDIS_URL=redis://:{redis_password}@redis:6379/0\n")
        existing.update(REDIS_PASSWORD=redis_password, REDIS_URL=f"redis://:{redis_password}@redis:6379/0")
    runtime = target.with_name(".env.preproduction.runtime")
    if runtime.exists():
        runtime_values = dict(line.split("=", 1) for line in runtime.read_text(encoding="utf-8").splitlines() if "=" in line and not line.startswith("#"))
        if not runtime_values.get("REDIS_PASSWORD"):
            with runtime.open("a", encoding="utf-8") as stream:
                stream.write(f"\nREDIS_PASSWORD={existing['REDIS_PASSWORD']}\nREDIS_URL={existing['REDIS_URL']}\n")
    else:
        with runtime.open("x", encoding="utf-8") as stream:
            stream.write("\n".join(f"{key}={value}" for key, value in existing.items() if key not in {"POSTGRES_PASSWORD", "MIGRATION_DATABASE_URL"}) + "\n")
    raise SystemExit("Existing database/account secrets preserved; Redis authentication configured")

database_password, app_password, storage_password, admin_password = (secrets.token_hex(24) for _ in range(4))
redis_password = secrets.token_hex(32)
values = {
    "POSTGRES_PASSWORD": database_password,
    "DATABASE_URL": f"postgresql+psycopg://saas_app:{app_password}@postgres:5432/saas_preproduction",
    "MIGRATION_DATABASE_URL": f"postgresql+psycopg://saas:{database_password}@postgres:5432/saas_preproduction",
    "APP_DB_USER": "saas_app", "APP_DB_PASSWORD": app_password,
    "REDIS_PASSWORD": redis_password, "REDIS_URL": f"redis://:{redis_password}@redis:6379/0", "EXECUTION_MODE": "worker",
    "AUTO_CREATE_SCHEMA": "false", "MAIL_DEBUG": "false", "SMTP_HOST": "mailpit",
    "BOOTSTRAP_ADMIN_EMAIL": "admin@example.test", "BOOTSTRAP_ADMIN_PASSWORD": admin_password,
    "FRONTEND_BASE_URL": "http://localhost:18080", "CORS_ORIGINS": "http://localhost:18080",
    "MODEL_MODE": "deterministic", "STORAGE_BACKEND": "s3", "S3_ENDPOINT": "http://minio:9000",
    "S3_ACCESS_KEY": "delivery", "S3_SECRET_KEY": storage_password, "S3_BUCKET": "delivery",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel:4318",
}
with target.open("x", encoding="utf-8") as stream:
    stream.write("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
print("Created .env.preproduction; passwords were not printed. Keep this ignored file private.")
with target.with_name(".env.preproduction.runtime").open("x", encoding="utf-8") as stream:
    stream.write("\n".join(f"{key}={value}" for key, value in values.items() if key not in {"POSTGRES_PASSWORD", "MIGRATION_DATABASE_URL"}) + "\n")
