"""Optional self-hosted retrieval service. Start separately from the SaaS API."""
import asyncio
import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from backend.config import get_settings
from backend.retrieval_models import chunks, embeddings, rank, token_counts
from backend.retrieval_queue import InferenceQueue


@asynccontextmanager
async def lifespan(app):
    queue = InferenceQueue(get_settings().retrieval_queue_size)
    app.state.inference = queue
    app.state.ready = False
    app.state.warmup_error = ""
    queue.start()

    async def warmup():
        try:
            settings = get_settings()
            if settings.embedding_mode != "local" or settings.reranker_mode != "local":
                raise ValueError("model service requires local mode")
            await queue.submit(lambda: embeddings(["检索服务预热"], query=True, _settings=s))
            await queue.submit(lambda: rank("成员导入", [{"text": "成员导入需先校验邮箱"}]))
            app.state.ready = True
        except Exception as exc:
            app.state.warmup_error = "MODEL_WARMUP_FAILED"
            logging.getLogger(__name__).error("Model warmup failed (%s)", type(exc).__name__)

    task = asyncio.create_task(warmup())
    try:
        yield
    finally:
        await task
        app.state.ready = False
        await queue.close()


app = FastAPI(title="SaaS retrieval models", docs_url=None, redoc_url=None, lifespan=lifespan)


def ready_queue():
    if not getattr(app.state, "ready", False):
        raise HTTPException(503, {"code": "MODEL_NOT_READY"})
    return app.state.inference


@app.get("/health/live")
async def live():
    return {"live": True}


@app.get("/health/ready")
async def ready():
    queue = ready_queue()
    s = get_settings()
    return {"ready": True, "real_models_verified": True, "device": s.embedding_device,
            "dimensions": s.embedding_dimensions, "queue_depth": queue.outstanding,
            "embedding_revision": s.embedding_revision, "reranker_revision": s.reranker_revision}


def authorize(kind):
    def check(authorization: str = Header(default="")):
        key = getattr(get_settings(), f"{kind}_api_key")
        if not key or not authorization.startswith("Bearer ") or not hmac.compare_digest(authorization[7:], key):
            raise HTTPException(401, "需要模型服务访问凭据")
    return check


class EmbeddingRequest(BaseModel):
    model: str = Field(min_length=1, max_length=300)
    input: list[str] = Field(min_length=1, max_length=64)
    background: bool = False


class RerankRequest(BaseModel):
    model: str = Field(min_length=1, max_length=300)
    query: str = Field(min_length=1, max_length=8000)
    documents: list[str] = Field(min_length=1, max_length=60)
    top_n: int = Field(default=60, ge=1, le=60)


class ChunkRequest(BaseModel):
    model: str = Field(min_length=1, max_length=300)
    text: str = Field(max_length=150000)
    size: int = Field(default=220, ge=32, le=480)
    overlap: int = Field(default=30, ge=0, le=120)


@app.post("/v1/chunks", dependencies=[Depends(authorize("embedding"))])
async def chunk_route(payload: ChunkRequest):
    s = get_settings()
    if s.embedding_mode != "local" or payload.model != s.embedding_local_model:
        raise HTTPException(503, "服务未配置此本地 Embedding 模型")
    if payload.overlap >= payload.size:
        raise HTTPException(422, "重叠必须小于分块长度")
    values = await ready_queue().submit(
        lambda: chunks(payload.text, payload.size, payload.overlap), background=True)
    return {"model": s.embedding_local_model, "revision": s.embedding_revision,
            "chunks": [{"ordinal": i, "heading": heading, "text": text}
                       for i, (heading, text) in enumerate(values)]}


@app.post("/v1/embeddings", dependencies=[Depends(authorize("embedding"))])
async def embed_route(payload: EmbeddingRequest):
    s = get_settings()
    if s.embedding_mode != "local" or payload.model != s.embedding_local_model:
        raise HTTPException(503, "服务未配置此本地 Embedding 模型")
    if any(not text.strip() or len(text) > 12000 for text in payload.input):
        raise HTTPException(422, "文本为空或超过单条 12000 字符限制")
    # Inputs already include the caller's query template where applicable.
    values = await ready_queue().submit(lambda: embeddings(payload.input, _settings=s), background=payload.background)
    return {"model": s.embedding_local_model, "revision": s.embedding_revision,
            "data": [{"index": i, "embedding": value} for i, value in enumerate(values)]}


@app.post("/v1/rerank", dependencies=[Depends(authorize("reranker"))])
async def rerank_route(payload: RerankRequest):
    s = get_settings()
    if s.reranker_mode != "local" or payload.model != s.reranker_local_model:
        raise HTTPException(503, "服务未配置此本地重排模型")
    if any(len(text) > 12000 for text in payload.documents):
        raise HTTPException(422, "文档超过单条 12000 字符限制")
    hits = [{"id": i, "text": text} for i, text in enumerate(payload.documents)]
    ordered = await ready_queue().submit(lambda: rank(payload.query, hits, with_scores=True))
    return {"model": s.reranker_local_model, "revision": s.reranker_revision,
            "results": [{"index": h["id"], "score": h["rerank_score"]} for h in ordered[:payload.top_n]]}


class TokenCountRequest(BaseModel):
    model: str
    texts: list[str] = Field(min_length=1, max_length=128)


@app.post('/v1/token-counts', dependencies=[Depends(authorize('embedding'))])
async def count_route(payload: TokenCountRequest):
    s = get_settings()
    if s.embedding_mode != 'local' or payload.model != s.embedding_local_model:
        raise HTTPException(503, 'Token 计数模型不匹配')
    if sum(len(t) for t in payload.texts) > 200000:
        raise HTTPException(422, 'Token 计数请求过大')
    counts = await ready_queue().submit(lambda: token_counts(payload.texts))
    return {'model': s.embedding_local_model, 'revision': s.embedding_revision, 'counts': counts}
