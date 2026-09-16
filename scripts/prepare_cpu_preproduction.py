"""Prepare isolated CPU deployment settings; never print credentials."""
import secrets
from pathlib import Path

from dotenv import dotenv_values, set_key
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parent.parent
GENERATION_KEYS = ("MODEL_BASE_URL", "MODEL_NAME", "MODEL_API_KEY", "MODEL_API_STYLE",
                   "LLM_MODE", "LLM_LOCAL_MODEL", "LLM_LOCAL_BASE_URL", "LLM_LOCAL_API_KEY")
OWNER_KEYS = {"POSTGRES_PASSWORD", "MIGRATION_DATABASE_URL"}


def read(path):
    return {key: value for key, value in dotenv_values(path, interpolate=False).items() if value is not None}


def save(path, values):
    for value in values.values():
        if "\n" in value or "\r" in value:
            raise ValueError("Multiline deployment values are not supported")
    path.touch(exist_ok=True)
    for key, value in values.items():
        set_key(path, key, value, quote_mode="always")


def prepare(root=ROOT):
    target = root / ".env.preproduction"
    values = read(target)
    for key in ("POSTGRES_PASSWORD", "APP_DB_PASSWORD", "S3_SECRET_KEY", "BOOTSTRAP_ADMIN_PASSWORD",
                "JWT_SECRET", "EMBEDDING_API_KEY", "RERANKER_API_KEY"):
        if not values.get(key):
            values[key] = secrets.token_hex(32)
    for key, value in {"APP_DB_USER": "saas_app", "S3_ACCESS_KEY": "delivery",
                       "BOOTSTRAP_ADMIN_EMAIL": "admin@example.test"}.items():
        values.setdefault(key, value)
    values.setdefault("DATABASE_URL", f"postgresql+psycopg://{values['APP_DB_USER']}:{values['APP_DB_PASSWORD']}@postgres:5432/saas_preproduction")
    values.setdefault("MIGRATION_DATABASE_URL", f"postgresql+psycopg://saas:{values['POSTGRES_PASSWORD']}@postgres:5432/saas_preproduction")
    for key in ("DATABASE_URL", "MIGRATION_DATABASE_URL"):
        url = make_url(values[key])
        if url.host != "postgres" or url.database != "saas_preproduction":
            raise ValueError("Preproduction must use the isolated postgres/saas_preproduction database")
    original = read(root / ".env")
    for key in GENERATION_KEYS:
        if not values.get(key) and original.get(key):
            values[key] = original[key]
    values.update({
        "REDIS_URL": "redis://redis:6379/0", "EXECUTION_MODE": "worker", "AGENT_ENGINE": "v2",
        "AUTO_CREATE_SCHEMA": "false", "MAIL_DEBUG": "false", "SMTP_HOST": "mailpit",
        "SMTP_PORT": "1025", "SMTP_STARTTLS": "false", "SMTP_USERNAME": "", "SMTP_PASSWORD": "",
        "FRONTEND_BASE_URL": "http://localhost:18080", "CORS_ORIGINS": "http://localhost:18080",
        "STORAGE_BACKEND": "s3", "S3_ENDPOINT": "http://minio:9000", "S3_BUCKET": "delivery",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel:4318",
        "MODEL_MODE": "real", "RAG_MODE": "real", "EMBEDDING_MODE": "online", "RERANKER_MODE": "online",
        "EMBEDDING_MODEL": "BAAI/bge-small-zh-v1.5", "EMBEDDING_DIMENSIONS": "512",
        "EMBEDDING_REVISION": "7999e1d3359715c523056ef9478215996d62a620",
        "RERANKER_MODEL": "BAAI/bge-reranker-base",
        "RERANKER_REVISION": "2cfc18c9415c912f9d8155881c133215df768a70",
        "EMBEDDING_BASE_URL": "http://retrieval:8010/v1", "RERANKER_BASE_URL": "http://retrieval:8010/v1",
        "OPENSEARCH_URL": "http://opensearch:9200", "OPENSEARCH_INDEX": "saas-rag-cpu-v2",
        "KNOWLEDGE_INDEX_VERSION": "chunks-bge-cpu-v2", "EMBEDDING_QUERY_STYLE": "prefix",
        "EMBEDDING_QUERY_INSTRUCTION": "为这个句子生成表示以用于检索相关文章：",
    })
    runtime = root / ".env.preproduction.runtime"
    runtime_values = {key: value for key, value in values.items() if key not in OWNER_KEYS}
    save(target, values)
    # Derived files are projections: remove stale owner keys rather than merging.
    runtime.write_text("", encoding="utf-8")
    save(runtime, runtime_values)
    model_env = root / ".env.preproduction.models"
    model_env.write_text("", encoding="utf-8")
    save(model_env, {key: values[key] for key in ("EMBEDDING_API_KEY", "RERANKER_API_KEY")})
    return {"prepared": True, "generation_configured": bool(values.get("MODEL_API_KEY") or values.get("LLM_LOCAL_BASE_URL"))}


if __name__ == "__main__":
    print(prepare())
