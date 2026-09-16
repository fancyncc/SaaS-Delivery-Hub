"""Single-process local indexing scheduler. Production uses the Celery indexer."""
import asyncio
import logging


async def maintain_indexes():
    from backend.worker import index_knowledge
    while True:
        try:
            await index_knowledge()
        except Exception:
            logging.getLogger(__name__).exception("Local indexing pass failed")
        await asyncio.sleep(5)
