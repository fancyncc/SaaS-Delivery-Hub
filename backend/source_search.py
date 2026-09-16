"""BM25 for unified sources; only explicitly authorized source IDs are searched."""
import json

from fastapi import HTTPException

from backend.config import get_settings
from backend.lexical_index import client


def index():
    return get_settings().opensearch_index + "-sources"


async def remove(source_id):
    async with client() as http:
        response = await http.post(f"/{index()}/_delete_by_query?refresh=true&conflicts=proceed",
            json={"query": {"term": {"source_id": source_id}}})
        if response.status_code != 404:
            response.raise_for_status()


async def publish(source, chunks):
    async with client() as http:
        response = await http.put(f"/{index()}", json={"mappings": {"properties": {
            "tenant_id": {"type": "keyword"}, "source_id": {"type": "keyword"},
            "generation": {"type": "integer"},
            "lexemes": {"type": "text", "analyzer": "whitespace", "similarity": "BM25"}}}})
        if response.status_code != 200:
            if response.status_code != 400 or response.json().get("error", {}).get("type") != "resource_already_exists_exception":
                response.raise_for_status()
    await remove(source.id)
    lines = []
    for chunk in chunks:
        lines.extend([json.dumps({"index": {"_index": index(), "_id": chunk.id}}),
                      json.dumps({"tenant_id": source.tenant_id, "source_id": source.id,
                          "generation": source.generation, "lexemes": chunk.lexemes}, ensure_ascii=False)])
    if lines:
        async with client() as http:
            response = await http.post("/_bulk?refresh=wait_for", content="\n".join(lines) + "\n",
                headers={"Content-Type": "application/x-ndjson"})
            response.raise_for_status()
            if response.json().get("errors", True):
                raise HTTPException(503, {"code": "INDEX_PUBLISH_FAILED"})


async def search(tenant_id, source_ids, query):
    if not source_ids:
        return []
    async with client() as http:
        response = await http.post(f"/{index()}/_search", json={"size": 30, "_source": False,
            "query": {"bool": {"filter": [{"term": {"tenant_id": tenant_id}},
                {"terms": {"source_id": source_ids}}], "must": [{"match": {"lexemes": query}}]}}})
        response.raise_for_status()
        try:
            identifiers = [hit["_id"] for hit in response.json()["hits"]["hits"]]
            if not all(isinstance(value, str) for value in identifiers):
                raise ValueError("invalid identifiers")
            return list(dict.fromkeys(identifiers))
        except (KeyError, TypeError, ValueError):
            raise HTTPException(502, {"code": "INDEX_RESPONSE_INVALID"}) from None
