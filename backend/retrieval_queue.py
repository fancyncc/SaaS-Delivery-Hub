"""One bounded, priority-ordered inference worker per model-service process."""
import asyncio
import itertools
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException


class InferenceQueue:
    def __init__(self, capacity: int = 32):
        self.capacity = capacity
        self.outstanding = 0
        self.active = False
        self._queue: asyncio.PriorityQueue[tuple[int, int, Callable[[], Awaitable[Any]] | None, asyncio.Future[Any] | None]] = asyncio.PriorityQueue()
        self._order = itertools.count()
        self._worker: asyncio.Task[None] | None = None
        self._closing = False

    def start(self):
        self._worker = asyncio.create_task(self._run())

    async def submit(self, operation: Callable[[], Awaitable], *, background=False):
        if self._closing or self._worker is None or self._worker.done():
            raise HTTPException(503, {"code": "MODEL_NOT_READY"})
        if self.outstanding >= self.capacity:
            raise HTTPException(503, {"code": "MODEL_BUSY"}, headers={"Retry-After": "2"})
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self.outstanding += 1
        self._queue.put_nowait((1 if background else 0, next(self._order), operation, future))
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            # Skip a disconnected request if still queued. An active inference must
            # finish before the next operation starts; cancellation cannot stop torch.
            future.cancel()
            raise

    async def _run(self):
        while True:
            _, _, operation, future = await self._queue.get()
            if operation is None:
                self._queue.task_done()
                return
            assert future is not None
            try:
                if not future.cancelled():
                    self.active = True
                    try:
                        result = await operation()
                    except Exception as exc:
                        if not future.done():
                            future.set_exception(exc)
                    else:
                        if not future.done():
                            future.set_result(result)
            finally:
                self.active = False
                self.outstanding -= 1
                self._queue.task_done()

    async def close(self):
        self._closing = True
        if self._worker is not None:
            self._queue.put_nowait((2, next(self._order), None, None))
            await self._worker
