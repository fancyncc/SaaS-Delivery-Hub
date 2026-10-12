"""Disposable Worker used only for a real process-kill recovery exercise."""
import asyncio
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from redis.asyncio import Redis

from backend import worker
from backend.config import get_settings

identifier = os.environ["ACCEPTANCE_KILL_TOKEN"]
if not re.fullmatch(r"[0-9a-f]{32}", identifier):
    raise RuntimeError("Invalid isolated acceptance identifier")
original = worker.advance


async def pause_after_committed_node(session, run, **kwargs):
    if run.state.get("completed_nodes") == ["create_project"]:
        async with Redis.from_url(get_settings().redis_url) as client:
            if await client.set(f"acceptance:kill-gate:{identifier}:{run.id}", "committed", nx=True, ex=600):
                # The orchestrator sends SIGKILL after verifying the committed node.
                while True:
                    await asyncio.sleep(0.25)
    return await original(session, run, **kwargs)


worker.advance = pause_after_committed_node
worker.celery.worker_main(["worker", "--pool=solo", "--concurrency=1", "--loglevel=WARNING"])
