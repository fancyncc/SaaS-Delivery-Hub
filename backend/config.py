import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def retrieval_path(value: str) -> str:
    """Resolve relocated retrieval files for existing deployment configuration."""
    return (value.replace("\\", "/")
            .replace("evaluations/v3/", "evaluations/retrieval/")
            .replace("rag.v3.multilingual.example.json", "retrieval.multilingual.example.json"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parents[1] / ".env", extra="ignore", populate_by_name=True)

    @field_validator("retrieval_release", "retrieval_model_profile", mode="before")
    @classmethod
    def normalize_retrieval_path(cls, value):
        if isinstance(value, str):
            return retrieval_path(value)
        return value

    app_name: str = "SaaS 实施平台"
    database_url: str = "sqlite+aiosqlite:///./saas_agent.db"
    migration_database_url: str = ""
    app_db_user: str = "saas_app"
    app_db_password: str = "saas_app"
    jwt_secret: str = "development-only-secret-change-me-32-bytes"
    cors_origins: str = "http://localhost:5173,http://localhost:8080"
    demo_mode: bool = False
    auto_create_schema: bool = False
    cookie_secure: bool = False
    session_hours: int = 8
    redis_url: str = "redis://localhost:6379/0"
    bootstrap_admin_email: str = "admin@example.com"
    bootstrap_admin_password: str = "ChangeMe123!"
    bootstrap_admin_name: str = "平台管理员"
    legacy_tenant_name: str = "Legacy Demo"
    mail_debug: bool = True
    require_verified_email_for_company: bool = True
    frontend_base_url: str = "http://localhost:8080"
    execution_mode: str = "inline"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = False
    smtp_from: str = "implementation@example.test"
    storage_backend: str = "database"
    s3_endpoint: str = "http://localhost:9000"
    s3_bucket: str = "delivery"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    model_mode: str = "deterministic"
    model_base_url: str = ""
    model_name: str = ""
    model_api_key: str = ""
    model_timeout: int = 30
    model_context_window: int = Field(default=32768, ge=2048)
    model_max_output_tokens: int = Field(default=4096, ge=128)
    model_tokenizer: str = ""  # explicit tiktoken:<encoding> or hf:<local path>
    model_tokenizer_map: dict[str, str] = Field(default_factory=dict)
    context_safety_ratio: float = Field(default=0.1, ge=0, le=0.5)
    chat_context_tokens: int = Field(default=12000, ge=1000)
    chat_context_mode: Literal["off", "shadow", "on"] = "off"
    chat_summary_tokens: int = Field(default=1200, ge=200)
    chat_context_local_worker: bool | None = None  # auto for inline; explicit false delegates to Celery
    chat_history_enabled: bool = False
    chat_memory_items_enabled: bool = False
    chat_memory_candidates_enabled: bool = False
    context_scoring_mode: Literal["off", "shadow", "on"] = "off"
    agent_experience_enabled: bool = False
    embedding_model: str = ""
    rag_mode: Literal["mock", "real"] = "mock"
    retrieval_indexing_enabled: bool = Field(default=False, validation_alias=AliasChoices("RETRIEVAL_INDEXING_ENABLED", "RAG_V3_INDEXING_ENABLED"))
    rag_pdf_enabled: bool = True
    rag_office_enabled: bool = True
    retrieval_relevance_mode: Literal["rules", "calibrated"] = Field(default="rules", validation_alias=AliasChoices("RETRIEVAL_RELEVANCE_MODE", "RAG_V3_RELEVANCE_MODE"))
    retrieval_min_rerank_score: float = Field(default=0.5, allow_inf_nan=False, validation_alias=AliasChoices("RETRIEVAL_MIN_RERANK_SCORE", "RAG_V3_MIN_RERANK_SCORE"))
    retrieval_same_language_min_evidence_score: float = Field(default=0.5, ge=0, le=1, validation_alias=AliasChoices("RETRIEVAL_SAME_LANGUAGE_MIN_EVIDENCE_SCORE", "RAG_V3_SAME_LANGUAGE_MIN_EVIDENCE_SCORE"))
    retrieval_en_to_zh_min_evidence_score: float = Field(default=0.35, ge=0, le=1, validation_alias=AliasChoices("RETRIEVAL_EN_TO_ZH_MIN_EVIDENCE_SCORE", "RAG_V3_EN_TO_ZH_MIN_EVIDENCE_SCORE"))
    retrieval_zh_to_en_min_evidence_score: float = Field(default=0.35, ge=0, le=1, validation_alias=AliasChoices("RETRIEVAL_ZH_TO_EN_MIN_EVIDENCE_SCORE", "RAG_V3_ZH_TO_EN_MIN_EVIDENCE_SCORE"))
    retrieval_mixed_language_min_evidence_score: float = Field(default=0.45, ge=0, le=1, validation_alias=AliasChoices("RETRIEVAL_MIXED_LANGUAGE_MIN_EVIDENCE_SCORE", "RAG_V3_MIXED_LANGUAGE_MIN_EVIDENCE_SCORE"))
    retrieval_release: str = Field(default="evaluations/retrieval/release.json", validation_alias=AliasChoices("RETRIEVAL_RELEASE", "RAG_V3_RELEASE"))
    retrieval_model_profile: str = Field(default="", validation_alias=AliasChoices("RETRIEVAL_MODEL_PROFILE", "RAG_V3_MODEL_PROFILE"))
    rag_inspection_calibration: str = "evaluations/inspection_calibration.json"
    local_indexer_enabled: bool = False
    model_api_style: Literal["responses", "chat_completions"] = "responses"
    llm_mode: Literal["local", "online"] = "online"
    llm_local_model: str = ""
    llm_local_base_url: str = ""
    llm_local_api_key: str = ""
    embedding_mode: Literal["local", "online"] = "local"
    embedding_local_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_device: str = "cpu"
    embedding_revision: str = "main"
    embedding_query_instruction: str = "为这个句子生成表示以用于检索相关文章："
    embedding_query_style: Literal["prefix", "instruct", "none"] = "prefix"
    embedding_max_length: int = Field(default=512, ge=32, le=512)
    embedding_batch_size: int = Field(default=16, ge=1, le=64)
    retrieval_cpu_threads: int = Field(default=4, ge=1, le=64)
    retrieval_queue_size: int = Field(default=32, ge=1, le=32)
    retrieval_model_cache: str = str(Path(__file__).resolve().parents[1] / "data" / "models")
    reranker_mode: Literal["local", "online"] = "local"
    reranker_local_model: str = "BAAI/bge-reranker-base"
    reranker_device: str = "cpu"
    reranker_revision: str = "main"
    reranker_batch_size: int = Field(default=4, ge=1, le=64)
    reranker_max_length: int = Field(default=256, ge=128, le=512)
    reranker_max_candidates: int = Field(default=16, ge=1, le=60)
    reranker_max_windows: int = Field(default=32, ge=1, le=120)
    knowledge_chunk_tokens: int = Field(default=220, ge=64, le=400)
    knowledge_chunk_overlap: int = Field(default=30, ge=0, le=63)
    knowledge_query_expansion: bool = True
    opensearch_url: str = ""
    opensearch_index: str = "saas-rag-v1"
    opensearch_username: str = ""
    opensearch_password: str = ""

    skill_runtime_enabled: bool = False
    skill_max_tokens: int = Field(default=8000, ge=100, le=16000)
    official_web_enabled: bool = False
    official_search_api_key: str = ""
    official_products: dict[str, dict] = Field(default_factory=dict)
    official_web_timeout: int = Field(default=15, ge=1, le=30)
    official_web_max_bytes: int = Field(default=500000, ge=1000, le=2000000)
    agent_max_rounds: int = Field(default=20, ge=1, le=100)
    agent_max_retries: int = Field(default=2, ge=0, le=5)
    agent_max_replans: int = Field(default=2, ge=0, le=5)
    agent_token_budget: int = Field(default=120000, ge=1000)
    agent_context_tokens: int = Field(default=12000, ge=1000)
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_dimensions: int = Field(default=512, ge=1, le=2000)
    knowledge_index_version: str = "chunks-bge-cpu-v2"
    knowledge_min_similarity: float = Field(default=0.35, ge=0, le=1)
    reranker_base_url: str = ""
    reranker_api_key: str = ""
    reranker_model: str = ""
    otel_exporter_otlp_endpoint: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings(_env_file=os.environ.get("SAAS_ENV_FILE") or Settings.model_config["env_file"])
