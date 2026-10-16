from __future__ import annotations

from collections import defaultdict, deque
from time import monotonic

from fastapi import HTTPException

try:
    from redis.asyncio import Redis
except ImportError:  # Local fallback; production images install redis from pyproject.toml.
    Redis = None  # type: ignore[assignment,misc]

from backend.config import get_settings

_redis = None
_fallback: dict[str, deque[float]] = defaultdict(deque)
_fallback_expiry: dict[str, float] = {}
_INCREMENT = "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end; return n"


async def enforce_rate_limit(key: str, limit: int, window_seconds: int) -> None:
    """Redis-backed limiter with an in-process fallback for local development."""
    global _redis
    try:
        if Redis is None:
            raise RuntimeError("redis client unavailable")
        if _redis is None:
            _redis = Redis.from_url(get_settings().redis_url, decode_responses=True, socket_connect_timeout=0.2, socket_timeout=0.2)
        count = await _redis.eval(_INCREMENT, 1, key, window_seconds)
        if count > limit:
            raise HTTPException(429, "请求过于频繁，请稍后重试", headers={"Retry-After": str(window_seconds)})
        return
    except HTTPException:
        raise
    except Exception:
        if get_settings().app_env == "production":
            raise HTTPException(503, "限流服务暂不可用，请稍后重试", headers={"Retry-After": "5"}) from None
        now = monotonic()
        for expired in [k for k, expiry in _fallback_expiry.items() if expiry <= now]:
            _fallback.pop(expired, None)
            _fallback_expiry.pop(expired, None)
        if key not in _fallback and len(_fallback) >= 10000:
            raise HTTPException(503, "限流服务繁忙", headers={"Retry-After": "5"}) from None
        _fallback_expiry[key] = now + window_seconds
        bucket = _fallback[key]
        while bucket and bucket[0] <= now - window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            raise HTTPException(429, "请求过于频繁，请稍后重试", headers={"Retry-After": str(window_seconds)}) from None
        bucket.append(now)


async def clear_rate_limit(key: str) -> None:
    _fallback.pop(key, None)
    _fallback_expiry.pop(key, None)
    if _redis is not None:
        try:
            await _redis.delete(key)
        except Exception:
            pass
