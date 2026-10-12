"""SSE adapter for the shared authorized conversation pipeline."""
import json
import logging

from fastapi import HTTPException


def event(kind, data):
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def generate(session, row, user, payload, api_schema, memo):
    from backend.chat_pipeline import reply
    try:
        async for kind, data in reply(session, row, user, payload, api_schema, memo):
            yield event(kind, data)
    except Exception as exc:
        await session.rollback()
        logging.getLogger(__name__).warning("Chat stream failed (%s)", type(exc).__name__)
        yield event("error", {"message": exc.detail if isinstance(exc, HTTPException) and isinstance(exc.detail, str)
            else "模型或检索服务不可用，本次回答未保存，请重试"})
