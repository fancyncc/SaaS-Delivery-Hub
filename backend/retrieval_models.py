"""Explicit local/HTTP retrieval models. No implicit model or credential fallback."""
import asyncio
import logging
import math
import threading
from typing import Any

import httpx
from fastapi import HTTPException

from backend.config import get_settings

_models: dict[tuple, Any] = {}
_lock = threading.Lock()


async def chunks(body: str, size: int = 220, overlap: int = 30) -> list[tuple[str, str]]:
    from backend.chunking import chunk_text

    s = get_settings()
    if s.embedding_mode == "local":
        def run():
            with _lock:
                return chunk_text(body, size, overlap, tokenizer=_local("embedding", s).tokenizer)
        return await asyncio.to_thread(run)
    if not s.embedding_base_url or not s.embedding_model:
        raise HTTPException(503, "分块服务未配置")
    async with httpx.AsyncClient(timeout=s.model_timeout, trust_env=False) as client:
        response = await client.post(s.embedding_base_url.rstrip("/") + "/chunks",
            headers={"Authorization": f"Bearer {s.embedding_api_key}"} if s.embedding_api_key else {},
            json={"model": s.embedding_model, "text": body, "size": size, "overlap": overlap})
        response.raise_for_status()
        try:
            payload = response.json()
            if payload["model"] != s.embedding_model or payload["revision"] != s.embedding_revision:
                raise ValueError("model identity")
            rows = payload["chunks"]
            if not isinstance(rows, list) or (body.strip() and not rows):
                raise ValueError("chunks")
            if any(not isinstance(row["heading"], str) or not isinstance(row["text"], str)
                   or not row["text"].strip() for row in rows):
                raise ValueError("chunk content")
            return [(row["heading"], row["text"]) for row in rows]
        except (KeyError, TypeError, ValueError):
            raise HTTPException(502, "分块服务返回格式或模型版本无效") from None


def query_input(value, settings):
    if settings.embedding_query_style == "instruct":
        return f"Instruct: {settings.embedding_query_instruction}\nQuery: {value}"
    if settings.embedding_query_style == "prefix":
        return settings.embedding_query_instruction + value
    return value


def passage(hit):
    return "\n".join(str(hit.get(key, "")) for key in ("title", "heading", "text") if hit.get(key))


def _local(kind, settings):
    name = getattr(settings, f"{kind}_local_model")
    device = getattr(settings, f"{kind}_device")
    revision = getattr(settings, f"{kind}_revision")
    key = (kind, name, device, revision, settings.reranker_max_length,
           settings.embedding_max_length, settings.retrieval_model_cache, settings.retrieval_cpu_threads)
    # The caller holds the shared inference lock. Use one service worker to avoid
    # loading duplicate weights and oversubscribing CPU threads across processes.
    if key not in _models:
        try:
            from sentence_transformers import CrossEncoder, SentenceTransformer
        except ImportError:
            raise HTTPException(503, "本地检索模型依赖未安装，请安装 rag-local 可选依赖") from None
        try:
            import torch
            if device == "cpu":
                torch.set_num_threads(settings.retrieval_cpu_threads)
            if kind == "embedding":
                _models[key] = SentenceTransformer(name, device=device, revision=revision,
                    cache_folder=settings.retrieval_model_cache, local_files_only=True)
                _models[key].max_seq_length = settings.embedding_max_length
            else:
                _models[key] = CrossEncoder(name, device=device, revision=revision,
                    max_length=settings.reranker_max_length,
                    cache_folder=settings.retrieval_model_cache, local_files_only=True)
        except Exception:
            logging.getLogger(__name__).exception("Local %s model initialization failed", kind)
            raise HTTPException(503, "本地模型加载失败，请检查已下载权重、缓存目录、版本与内存") from None
    return _models[key]


def validate_vectors(values, count, dimensions):
    if not isinstance(values, list) or len(values) != count:
        raise HTTPException(502, "向量服务返回数量无效")
    result = []
    for vector in values:
        if (not isinstance(vector, list) or len(vector) != dimensions
                or not all(type(x) in (int, float) and math.isfinite(x) for x in vector)):
            raise HTTPException(502, "向量输出维度或数值无效")
        norm = math.sqrt(sum(x * x for x in vector))
        if not math.isfinite(norm) or norm == 0:
            raise HTTPException(502, "向量输出范数无效")
        result.append([x / norm for x in vector])
    return result


async def embeddings(texts: list[str], *, query=False, _settings=None) -> list[list[float]]:
    s = _settings or get_settings()
    if not texts:
        return []
    if s.embedding_dimensions != 512 and _settings is None:
        raise HTTPException(503, "当前索引仅支持 512 维，请执行 BGE 迁移并重建索引")
    inputs = [query_input(t, s) for t in texts] if query else texts
    if s.embedding_mode == "local":
        def run():
            with _lock:
                return _local("embedding", s).encode(inputs, batch_size=s.embedding_batch_size,
                    normalize_embeddings=True, show_progress_bar=False).tolist()
        try:
            values = await asyncio.to_thread(run)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "本地向量推理失败，请检查设备资源和模型版本") from None
    else:
        if not s.embedding_base_url or not s.embedding_model:
            raise HTTPException(503, "请独立配置 EMBEDDING_BASE_URL 和 EMBEDDING_MODEL")
        values = []
        async with httpx.AsyncClient(timeout=s.model_timeout, trust_env=False) as client:
            for start in range(0, len(inputs), s.embedding_batch_size):
                batch = inputs[start:start + s.embedding_batch_size]
                response = await client.post(s.embedding_base_url.rstrip("/") + "/embeddings",
                    headers={"Authorization": f"Bearer {s.embedding_api_key}"} if s.embedding_api_key else {},
                    json={"model": s.embedding_model, "input": batch, "background": not query})
                response.raise_for_status()
                try:
                    if _settings is not None and (response.json().get('model') != s.embedding_model or response.json().get('revision') != s.embedding_revision):
                        raise ValueError('V3 embedding model identity')
                    rows = response.json()["data"]
                    if (any(type(row["index"]) is not int for row in rows)
                            or sorted(row["index"] for row in rows) != list(range(len(batch)))):
                        raise ValueError("indices")
                    values.extend(row["embedding"] for row in sorted(rows, key=lambda row: row["index"]))
                except (KeyError, TypeError, ValueError):
                    raise HTTPException(502, "向量服务返回格式无效") from None
    return validate_vectors(values, len(texts), s.embedding_dimensions)


async def rank(query: str, hits: list[dict], *, with_scores: bool = False, _settings=None) -> list[dict]:
    if not hits:
        return []
    s = _settings or get_settings()
    if len(hits) > s.reranker_max_windows:
        raise HTTPException(422, "候选数量超过重排窗口预算")
    if s.reranker_mode == "local":
        def run():
            with _lock:
                model = _local("reranker", s)
                tokenizer = model.tokenizer
                q = tokenizer.encode(query, add_special_tokens=False)[:64]
                width = s.reranker_max_length - len(q) - tokenizer.num_special_tokens_to_add(pair=True)
                pairs, owners = [], []
                windows = []
                for hit in hits:
                    tokens = tokenizer.encode(passage(hit), add_special_tokens=False)
                    starts = [0]
                    while starts[-1] + width < len(tokens) and len(starts) < s.reranker_max_windows:
                        starts.append(starts[-1] + max(1, width - 30))
                    windows.append([tokenizer.decode(tokens[start:start + width]) for start in starts])
                # Round-robin: every candidate gets one window before extras consume
                # the remaining budget. No unbounded inference on long attachments.
                for window in range(max(map(len, windows))):
                    for i, parts in enumerate(windows):
                        if window < len(parts) and len(pairs) < s.reranker_max_windows:
                            pairs.append((tokenizer.decode(q), parts[window]))
                            owners.append(i)
                    if len(pairs) >= s.reranker_max_windows:
                        break
                scores = model.predict(pairs, batch_size=s.reranker_batch_size, show_progress_bar=False).tolist()
                merged = [float("-inf")] * len(hits)
                for owner, score in zip(owners, scores, strict=True):
                    if not math.isfinite(score):
                        raise HTTPException(502, "重排模型返回无效分数")
                    merged[owner] = max(merged[owner], score)
                return sorted(range(len(hits)), key=lambda i: (-merged[i], i)), merged
        try:
            indices, merged = await asyncio.to_thread(run)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "本地重排推理失败，请检查设备资源和模型版本") from None
    else:
        if not s.reranker_base_url or not s.reranker_model:
            raise HTTPException(503, "请独立配置 RERANKER_BASE_URL 和 RERANKER_MODEL")
        async with httpx.AsyncClient(timeout=s.model_timeout, trust_env=False) as client:
            response = await client.post(s.reranker_base_url.rstrip("/") + "/rerank",
                headers={"Authorization": f"Bearer {s.reranker_api_key}"} if s.reranker_api_key else {},
                json={"model": s.reranker_model, "query": query,
                      "documents": [passage(h) for h in hits], "top_n": len(hits)})
            response.raise_for_status()
            try:
                indices = [item["index"] for item in response.json()["results"]]
                if any(type(i) is not int for i in indices) or sorted(indices) != list(range(len(hits))):
                    raise ValueError("permutation")
                if with_scores:
                    body = response.json()
                    if body.get("model") != s.reranker_model or body.get("revision") != s.reranker_revision:
                        raise ValueError("model identity")
                    merged = [0.0] * len(hits)
                    for item in body["results"]:
                        value = item.get("score")
                        if type(value) not in (int, float) or not math.isfinite(value):
                            raise ValueError("score capability")
                        merged[item["index"]] = value
            except (KeyError, TypeError, ValueError):
                raise HTTPException(502, "重排服务能力不匹配：需要完整候选、有效分数及匹配的模型版本") from None
    return [{**hits[i], "rerank_score": merged[i], "rerank_model": s.reranker_local_model if s.reranker_mode == "local" else s.reranker_model,
             "rerank_revision": s.reranker_revision} if with_scores else hits[i] for i in indices]


async def token_counts(texts: list[str], *, _settings=None) -> list[int]:
    s = _settings or get_settings()
    if s.embedding_mode == "local":
        def run():
            with _lock:
                tokenizer = _local("embedding", s).tokenizer
                return [len(tokenizer.encode(t, add_special_tokens=False)) for t in texts]
        return await asyncio.to_thread(run)
    async with httpx.AsyncClient(timeout=s.model_timeout, trust_env=False) as client:
        response = await client.post(s.embedding_base_url.rstrip('/') + '/token-counts',
            headers={"Authorization": f"Bearer {s.embedding_api_key}"}, json={"model": s.embedding_model, "texts": texts})
        response.raise_for_status()
        body = response.json()
        counts = body.get('counts')
        if (body.get('model') != s.embedding_model or body.get('revision') != s.embedding_revision
                or not isinstance(counts, list) or len(counts) != len(texts)
                or any(type(n) is not int or n < 0 for n in counts)):
            raise HTTPException(502, 'Token 计数服务能力或模型版本不匹配')
        return counts
