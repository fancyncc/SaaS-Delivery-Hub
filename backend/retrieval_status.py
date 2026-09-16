"""Administrator-only, bounded readiness probes without credentials or source text."""
import asyncio

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import text

from backend.config import get_settings
from backend.db import get_session
from backend.security import require_platform_roles

router = APIRouter(prefix="/api/platform/retrieval", tags=["retrieval-operations"])


async def probe_http(url, *, model=False):
    if not url:
        return False
    try:
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
            if model:
                return payload.get("ready") is True and payload.get("real_models_verified") is True and payload.get("dimensions") == 512
            return payload.get("status") in {"green", "yellow"}
    except (httpx.HTTPError, ValueError):
        return False


async def probe_redis():
    from redis.asyncio import Redis
    try:
        async with Redis.from_url(get_settings().redis_url, socket_connect_timeout=3, socket_timeout=3) as client:
            return bool(await client.ping())
    except Exception:
        return False


@router.get("/status", dependencies=[Depends(require_platform_roles("platform_super_admin"))])
async def status(session=Depends(get_session)):
    s = get_settings()
    database = False
    if session.bind.dialect.name == "postgresql":
        try:
            async with asyncio.timeout(3):
                version = await session.scalar(text("SELECT extversion FROM pg_extension WHERE extname='vector'"))
                database = bool(version) and tuple(int(part) for part in version.split(".")[:2]) >= (0, 8)
        except Exception:
            database = False
    embedding, reranker, search, redis = await asyncio.gather(
        probe_http(s.embedding_base_url.rstrip("/").removesuffix("/v1") + "/health/ready", model=True) if s.embedding_base_url else asyncio.sleep(0, result=False),
        probe_http(s.reranker_base_url.rstrip("/").removesuffix("/v1") + "/health/ready", model=True) if s.reranker_base_url else asyncio.sleep(0, result=False),
        probe_http(s.opensearch_url.rstrip("/") + "/_cluster/health") if s.opensearch_url else asyncio.sleep(0, result=False),
        probe_redis())
    services = {"postgres_vector": database, "opensearch": search, "redis": redis,
                "embedding": embedding, "reranker": reranker}
    verified = embedding and reranker
    return {"data": {"mode": s.rag_mode, "services": services, "real_models_verified": verified,
                     "ready_for_real_rag": s.rag_mode == "real" and s.embedding_dimensions == 512 and all(services.values())}}
