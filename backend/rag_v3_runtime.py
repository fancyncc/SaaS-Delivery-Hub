"""V3 model settings are copied, never mutate the chat/Agent singleton."""

import json
from pathlib import Path

from backend import retrieval_models
from backend.config import get_settings


def settings():
    s = get_settings()
    if not s.rag_v3_model_profile:
        return s.model_copy()
    profile = json.loads(Path(s.rag_v3_model_profile).read_text(encoding="utf-8"))
    allowed = {k: v for k, v in profile.items() if k.startswith(("embedding_", "reranker_"))}
    return type(s).model_validate({**s.model_dump(), **allowed})


async def embeddings(texts, *, query=False):
    return await retrieval_models.embeddings(texts, query=query, _settings=settings())


async def rank(query, hits, *, with_scores=True):
    return await retrieval_models.rank(query, hits, with_scores=with_scores, _settings=settings())


async def token_counts(texts):
    return await retrieval_models.token_counts(texts, _settings=settings())
