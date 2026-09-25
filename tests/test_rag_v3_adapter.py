import pytest
from fastapi import HTTPException

from backend import rag_v3_adapter


async def test_adapter_exposes_only_selected_evidence(monkeypatch):
    async def inspected(session, tenant, query, projects, budget):
        assert (tenant, projects, budget) == ("tenant-a", ["project-a"], 1200)
        return {"status": "evidence_found", "pending_documents": 0,
                "chunks": [{"id": "unit-1", "origin_id": "doc-1", "title": "项目说明",
                            "text": "最终证据", "version": 2, "source": "项目知识库",
                            "heading": "范围", "location": {"page": 3},
                            "source_chunk_ids": ["unit-1"]}],
                "diagnostics": {"candidates": [{"id": "unit-2", "text": "未选中的候选"}]}}

    monkeypatch.setattr(rag_v3_adapter, "inspect", inspected)
    hits = await rag_v3_adapter.retrieve_knowledge(None, "tenant-a", "范围是什么", project_ids=["project-a"], limit=3)
    assert [hit["id"] for hit in hits] == ["unit-1"]
    assert hits[0]["location"] == {"page": 3}
    assert "未选中的候选" not in str(hits)


async def test_pending_v3_index_never_falls_back(monkeypatch):
    async def inspected(*args):
        return {"status": "index_unavailable", "pending_documents": 1, "chunks": []}

    monkeypatch.setattr(rag_v3_adapter, "inspect", inspected)
    with pytest.raises(HTTPException) as exc:
        await rag_v3_adapter.retrieve_knowledge(None, "tenant-a", "资料内容", project_ids=[])
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "V3_INDEX_NOT_READY"
