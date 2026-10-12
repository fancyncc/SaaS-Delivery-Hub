"""Compatibility checks for the single Agent and shared knowledge index."""
import uuid

import pytest
from sqlalchemy import select
from test_delivery import preparing

from backend.config import Settings, retrieval_path
from backend.db import SessionLocal
from backend.models import AgentRun, AgentStep
from backend.run_snapshots import normalize_runtime_identity, runtime_identity


def test_environment_aliases_keep_existing_retrieval_configuration(monkeypatch):
    for key in ("RETRIEVAL_INDEXING_ENABLED", "RETRIEVAL_MIN_RERANK_SCORE", "RETRIEVAL_RELEASE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("RAG_V3_INDEXING_ENABLED", "true")
    monkeypatch.setenv("RAG_V3_MIN_RERANK_SCORE", "0.7")
    monkeypatch.setenv("RAG_V3_RELEASE", "evaluations/v3/release.json")
    settings = Settings(_env_file=None)
    assert settings.retrieval_indexing_enabled
    assert settings.retrieval_min_rerank_score == 0.7
    assert settings.retrieval_release == "evaluations/retrieval/release.json"
    assert "agent_engine" not in Settings.model_fields
    monkeypatch.setenv("RETRIEVAL_MIN_RERANK_SCORE", "0.8")
    assert Settings(_env_file=None).retrieval_min_rerank_score == 0.8


def test_snapshot_alias_normalization_preserves_semantic_configuration_checks():
    current = runtime_identity()
    old = {key.replace("retrieval_", "rag_v3_", 1) if key.startswith("retrieval_") else key: value
           for key, value in current.items()}
    old["rag_v3_release"] = "evaluations/v3/release.json"
    assert normalize_runtime_identity(old) == normalize_runtime_identity(current)
    old["rag_v3_min_rerank_score"] = 0.9
    assert normalize_runtime_identity(old) != normalize_runtime_identity(current)
    assert old["rag_v3_release"] == "evaluations/v3/release.json"


def test_relocated_model_profiles_accept_previous_windows_paths():
    assert retrieval_path(r"config\rag.v3.multilingual.example.json") == "config/retrieval.multilingual.example.json"
    assert Settings(_env_file=None,
        retrieval_model_profile=r"config\rag.v3.multilingual.example.json").retrieval_model_profile == "config/retrieval.multilingual.example.json"


@pytest.mark.parametrize("gate", ["approval", "materials"])
async def test_historical_run_continues_without_replaying_committed_nodes(client, gate):
    p, run_id, approver = await preparing(client, approvals_count=1 if gate == "approval" else 2)
    try:
        async with SessionLocal() as session:
            run = await session.get(AgentRun, run_id)
            # A saved task from the earlier fixed executor has no Agent state.
            run.state = {key: value for key, value in run.state.items() if key != "agent"}
            run.state = {**run.state, "engine_version": "legacy"}
            before = list(await session.scalars(select(AgentStep).where(
                AgentStep.run_id == run_id, AgentStep.node == "apply_configuration")))
            await session.commit()
        detail = (await client.get(f"/api/runs/{run_id}")).json()["data"]
        assert "engine_version" not in detail["state"]
        if gate == "approval":
            approval = next(a for a in (await approver.get(f"/api/approvals?run_id={run_id}")).json()["data"]
                            if a["status"] == "pending")
            response = await approver.post(f"/api/approvals/{approval['id']}/decision",
                headers={"Idempotency-Key": uuid.uuid4().hex},
                json={"decision": "approved", "expected_version": approval["version"]})
            assert response.status_code == 200, response.text
            assert response.json()["data"]["run_status"] == "preparing_materials"
        else:
            response = await client.post("/api/imports/validate", headers={"Idempotency-Key": uuid.uuid4().hex},
                json={"project_id": p["id"], "run_id": run_id,
                      "csv_text": "name,email,department,role\nA,a@example.com,设计部,admin"})
            assert response.status_code == 200 and response.json()["data"]["valid"], response.text
        async with SessionLocal() as session:
            run = await session.get(AgentRun, run_id)
            assert "engine_version" not in run.state and run.state["agent"]["plan"]
            after = list(await session.scalars(select(AgentStep).where(
                AgentStep.run_id == run_id, AgentStep.node == "apply_configuration")))
            assert len(after) == 1
            if before:
                assert [s.id for s in after] == [s.id for s in before]
    finally:
        await approver.aclose()


def test_api_has_one_reindex_entry_and_no_versioned_paths():
    from backend.main import app
    paths = app.openapi()["paths"]
    assert "/api/knowledge/{document_id}/reindex" in paths
    assert not any("/v3-index" in path for path in paths)
