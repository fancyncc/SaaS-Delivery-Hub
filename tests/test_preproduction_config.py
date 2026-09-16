from dotenv import dotenv_values

from scripts.prepare_cpu_preproduction import prepare


def test_preparation_is_idempotent_and_credentials_are_separated(tmp_path):
    original = "MODEL_API_KEY='generation-test-secret'\nMODEL_NAME='existing-model'\n"
    (tmp_path / ".env").write_text(original, encoding="utf-8")
    prepare(tmp_path)
    first = dotenv_values(tmp_path / ".env.preproduction")
    prepare(tmp_path)
    assert dotenv_values(tmp_path / ".env.preproduction") == first
    assert (tmp_path / ".env").read_text(encoding="utf-8") == original
    runtime = dotenv_values(tmp_path / ".env.preproduction.runtime")
    assert "POSTGRES_PASSWORD" not in runtime and "MIGRATION_DATABASE_URL" not in runtime
    assert runtime["EMBEDDING_DIMENSIONS"] == "512" and runtime["RAG_MODE"] == "real"
    assert runtime["MODEL_API_KEY"] == "generation-test-secret"
    models = dotenv_values(tmp_path / ".env.preproduction.models")
    assert set(models) == {"EMBEDDING_API_KEY", "RERANKER_API_KEY"}
    assert models["EMBEDDING_API_KEY"] == runtime["EMBEDDING_API_KEY"]


async def test_readiness_is_platform_admin_only(client, platform_client, monkeypatch):
    from backend import retrieval_status
    from backend.config import get_settings

    async def successful(*args, **kwargs):
        return True

    monkeypatch.setattr(get_settings(), "rag_mode", "mock")
    monkeypatch.setattr(retrieval_status, "probe_http", successful)
    monkeypatch.setattr(retrieval_status, "probe_redis", successful)
    response = await client.get("/api/platform/retrieval/status")
    assert response.status_code == 403
    response = await platform_client.get("/api/platform/retrieval/status")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["ready_for_real_rag"] is False
    assert "password" not in response.text.lower() and "api_key" not in response.text.lower()
