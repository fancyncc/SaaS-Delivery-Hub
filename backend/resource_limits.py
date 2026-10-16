"""Bound expensive work before invoking model or parser dependencies."""
import secrets
import time
from contextlib import asynccontextmanager

from fastapi import Depends, HTTPException

from backend import rate_limit
from backend.config import get_settings
from backend.security import Principal, current_principal

_leases: dict[str, dict[str, float]] = {}
_ACQUIRE = """
local now=tonumber(ARGV[1])
for i,key in ipairs(KEYS) do
 redis.call('ZREMRANGEBYSCORE',key,'-inf',now)
 if redis.call('ZCARD',key)>=tonumber(ARGV[i+3]) then return 0 end
end
for _,key in ipairs(KEYS) do
 redis.call('ZADD',key,now+tonumber(ARGV[2]),ARGV[3]); redis.call('EXPIRE',key,ARGV[2])
end
return 1
"""


@asynccontextmanager
async def work_budget(user: Principal, kind: str):
    scope = user.tenant_id or user.user_id
    await rate_limit.enforce_rate_limit(f"work:{kind}:user:{user.user_id}", 12, 60)
    await rate_limit.enforce_rate_limit(f"work:{kind}:tenant:{scope}", 60, 60)
    await rate_limit.enforce_rate_limit(f"work:{kind}:daily:{scope}", 2000, 86400)
    keys = [f"work:active:user:{user.user_id}", f"work:active:tenant:{scope}", "work:active:global"]
    limits = [2, 6, 24]
    token, now, remote = secrets.token_hex(16), time.time(), False
    try:
        if rate_limit._redis is None:
            raise RuntimeError("redis unavailable")
        acquired = await rate_limit._redis.eval(_ACQUIRE, len(keys), *keys, now, 900, token, *limits)
        remote = True
    except Exception:
        if get_settings().app_env == "production":
            raise HTTPException(503, "资源配额服务暂不可用", headers={"Retry-After": "5"}) from None
        for key in list(_leases):
            _leases[key] = {t: expiry for t, expiry in _leases[key].items() if expiry > now}
            if not _leases[key]:
                del _leases[key]
        acquired = all(len(_leases.get(key, {})) < limit for key, limit in zip(keys, limits, strict=True))
        if acquired:
            for key in keys:
                _leases.setdefault(key, {})[token] = now + 900
    if not acquired:
        raise HTTPException(429, "正在处理的任务过多，请稍后重试", headers={"Retry-After": "5"})
    try:
        yield
    finally:
        if remote and rate_limit._redis is not None:
            try:
                for key in keys:
                    await rate_limit._redis.zrem(key, token)
            except Exception:
                pass  # Expiring leases recover from network failures or worker death.
        else:
            for key in keys:
                _leases.get(key, {}).pop(token, None)


async def retrieval_budget(user: Principal = Depends(current_principal)):
    async with work_budget(user, "retrieval"):
        yield


async def upload_budget(user: Principal = Depends(current_principal)):
    async with work_budget(user, "upload"):
        yield
