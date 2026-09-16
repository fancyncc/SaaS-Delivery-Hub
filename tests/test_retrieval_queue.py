import asyncio

import pytest
from fastapi import HTTPException

from backend.retrieval_queue import InferenceQueue


async def test_queries_overtake_queued_indexing_without_interrupting_active_work():
    queue = InferenceQueue(3)
    queue.start()
    entered, release = asyncio.Event(), asyncio.Event()
    order = []

    async def running():
        entered.set()
        await release.wait()
        order.append("active")

    async def operation(name):
        order.append(name)

    active = asyncio.create_task(queue.submit(running, background=True))
    await entered.wait()
    indexing = asyncio.create_task(queue.submit(lambda: operation("index"), background=True))
    await asyncio.sleep(0)
    query = asyncio.create_task(queue.submit(lambda: operation("query")))
    await asyncio.sleep(0)
    with pytest.raises(HTTPException) as exc:
        await queue.submit(lambda: operation("overflow"))
    assert exc.value.detail["code"] == "MODEL_BUSY"
    release.set()
    await asyncio.gather(active, indexing, query)
    await queue.close()
    assert order == ["active", "query", "index"]
    assert queue.outstanding == 0


async def test_cancelled_client_does_not_overlap_running_inference():
    queue = InferenceQueue(2)
    queue.start()
    entered, release = asyncio.Event(), asyncio.Event()
    order = []

    async def first():
        entered.set()
        await release.wait()
        order.append("finished")

    async def next_operation():
        order.append("next")

    task = asyncio.create_task(queue.submit(first))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    next_task = asyncio.create_task(queue.submit(next_operation))
    await asyncio.sleep(0)
    assert order == []
    release.set()
    await next_task
    await queue.close()
    assert order == ["finished", "next"]


async def test_failure_releases_slot_and_worker_keeps_serving():
    queue = InferenceQueue(1)
    queue.start()

    async def failure():
        raise ValueError("test")

    async def success():
        return 512

    with pytest.raises(ValueError):
        await queue.submit(failure)
    assert await queue.submit(success) == 512
    await queue.close()


async def test_readiness_requires_successful_real_warmup(monkeypatch):
    from backend import retrieval_server as server
    entered, release = asyncio.Event(), asyncio.Event()

    async def embedding(*args, **kwargs):
        entered.set()
        await release.wait()
        return [[1.0] + [0.0] * 511]

    async def reranking(query, hits):
        return hits

    monkeypatch.setattr(server, "embeddings", embedding)
    monkeypatch.setattr(server, "rank", reranking)
    monkeypatch.setattr(server.get_settings(), "embedding_mode", "local")
    monkeypatch.setattr(server.get_settings(), "reranker_mode", "local")
    async with server.app.router.lifespan_context(server.app):
        await entered.wait()
        with pytest.raises(HTTPException):
            await server.ready()
        release.set()
        # Let both queued operations finish before inspecting readiness.
        for _ in range(20):
            await asyncio.sleep(0)
            if server.app.state.ready:
                break
        assert (await server.ready())["real_models_verified"] is True


async def test_failed_warmup_never_claims_ready(monkeypatch):
    from backend import retrieval_server as server

    async def unavailable(*args, **kwargs):
        raise RuntimeError("weights missing")

    monkeypatch.setattr(server, "embeddings", unavailable)
    async with server.app.router.lifespan_context(server.app):
        for _ in range(20):
            await asyncio.sleep(0)
            if server.app.state.warmup_error:
                break
        with pytest.raises(HTTPException):
            await server.ready()
        assert server.app.state.warmup_error == "MODEL_WARMUP_FAILED"
