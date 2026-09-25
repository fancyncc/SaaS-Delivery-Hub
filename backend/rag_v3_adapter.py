"""Translate final V3 evidence into the citation shape used by chat and Agent."""

from fastapi import HTTPException

from backend.rag_v3 import inspect, needs_retrieval


async def retrieve_knowledge(session, tenant_id, query, *, project_ids=(), limit=5):
    if not query.strip() or not needs_retrieval(query):
        return []
    result = await inspect(session, tenant_id, query, list(project_ids),
                           1200 if limit <= 3 else 1800)
    if result["status"] == "index_unavailable" and result["pending_documents"]:
        raise HTTPException(503, {"code": "V3_INDEX_NOT_READY"})
    # Only the assembler's selected evidence is eligible for an answer. In
    # particular, diagnostics.candidates are never citation sources.
    return [{"id": item["id"], "document_id": item["origin_id"],
             "title": item["title"], "text": item["text"],
             "version": item["version"], "source": item["source"],
             "heading": item.get("heading", ""), "kind": "customer_document",
             "location": item.get("location", {}),
             "source_chunk_ids": item["source_chunk_ids"]}
            for item in result["chunks"]]
