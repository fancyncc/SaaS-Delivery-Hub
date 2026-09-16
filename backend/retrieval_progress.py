"""Ephemeral progress; durable publication and permissions remain in PostgreSQL."""
import json
import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from backend.config import get_settings


def key(source):
    return f'retrieval:progress:{source.tenant_id}:{source.id}:{source.generation}:{source.attempts + 1}'


def client():
    return Redis.from_url(get_settings().redis_url, socket_connect_timeout=0.5, socket_timeout=0.5)


async def report(source):
    if get_settings().rag_mode != 'real':
        return
    try:
        async with client() as redis:
            await redis.set(key(source), json.dumps({'phase': source.phase, 'done': source.chunks_done,
                                                     'total': source.chunks_total}), ex=180)
    except RedisError:
        logging.getLogger(__name__).warning('Index progress unavailable')


async def read(source):
    if source.phase not in {'pending', 'failed'} or get_settings().rag_mode != 'real':
        return None
    try:
        async with client() as redis:
            value = await redis.get(key(source))
        return json.loads(value) if value else None
    except (RedisError, ValueError, TypeError):
        return None
