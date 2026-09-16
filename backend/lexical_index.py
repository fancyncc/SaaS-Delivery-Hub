"""BM25 index with pre-filtered scope; PostgreSQL revalidates all returned IDs."""
import json
import re

import httpx
from fastapi import HTTPException

from backend.config import get_settings


def client():
    s = get_settings()
    if not s.opensearch_url or not re.fullmatch(r"[a-z][a-z0-9_-]*", s.opensearch_index):
        raise HTTPException(503, "真实 BM25 检索需要有效的 OPENSEARCH_URL 和 OPENSEARCH_INDEX")
    return httpx.AsyncClient(base_url=s.opensearch_url.rstrip("/"), timeout=s.model_timeout, trust_env=False,
        auth=(s.opensearch_username, s.opensearch_password) if s.opensearch_username else None)


async def publish(document, pieces):
    s = get_settings()
    async with client() as http:
        # Idempotent index creation. Explicit whitespace analyzer consumes the
        # shared Chinese/identifier tokenizer instead of silently using English.
        response = await http.put(f"/{s.opensearch_index}", json={"mappings": {"properties": {
            "tenant_id": {"type": "keyword"}, "project_id": {"type": "keyword"},
            "document_id": {"type": "keyword"}, "index_version": {"type": "keyword"},
            "lexemes": {"type": "text", "analyzer": "whitespace", "similarity": "BM25"}}}})
        if response.status_code != 200:
            if response.status_code != 400 or response.json().get("error", {}).get("type") != "resource_already_exists_exception":
                response.raise_for_status()
        lines = []
        for piece in pieces:
            lines.extend([json.dumps({"index": {"_index": s.opensearch_index, "_id": piece.id}}),
                json.dumps({"tenant_id": document.tenant_id, "project_id": document.project_id or "company",
                    "document_id": document.id, "index_version": piece.index_version,
                    "lexemes": piece.lexemes}, ensure_ascii=False)])
        if not lines:
            return
        response = await http.post("/_bulk?refresh=wait_for", content="\n".join(lines) + "\n",
            headers={"Content-Type": "application/x-ndjson"})
        response.raise_for_status()
        if response.json().get("errors", True):
            raise HTTPException(503, "BM25 索引写入未全部成功，资料未发布")


async def search(tenant_id, project_ids, identity, query_lexemes, limit=30):
    s = get_settings()
    async with client() as http:
        response = await http.post(f"/{s.opensearch_index}/_search", json={"size": limit,
            "_source": False, "query": {"bool": {"filter": [
                {"term": {"tenant_id": tenant_id}}, {"term": {"index_version": identity}},
                {"terms": {"project_id": ["company", *(project_ids or [])]}}],
                "must": [{"match": {"lexemes": query_lexemes}}]}}})
        response.raise_for_status()
        try:
            ids = [row["_id"] for row in response.json()["hits"]["hits"]]
            if not all(isinstance(value, str) for value in ids):
                raise ValueError("id")
            return list(dict.fromkeys(ids))
        except (ValueError, KeyError, TypeError):
            raise HTTPException(502, "BM25 服务响应无效") from None
