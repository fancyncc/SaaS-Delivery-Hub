"""Version-filtered chunk retrieval shared by HTTP and workflow consumers."""
import asyncio
import hashlib
import json
import re
from uuid import uuid4

import httpx
from sqlalchemy import delete, exists, or_, select, text
from sqlalchemy.orm import aliased

from backend.agent_models import KnowledgePiece
from backend.chunking import chunk_text as chunk_text
from backend.config import get_settings
from backend.intelligence import embed
from backend.models import KnowledgeDocument
from backend.rag import search


def lexemes(value: str) -> str:
    # Chinese overlapping words avoid treating an entire unspaced sentence as one
    # PostgreSQL 'simple' token. Latin identifiers/error codes remain intact.
    words = re.findall(r"[a-z0-9_]+(?:[-.][a-z0-9_]+)*|[\u4e00-\u9fff]+", value.lower())
    tokens: list[str] = []
    for word in words:
        if re.fullmatch(r"[\u4e00-\u9fff]+", word):
            tokens.extend(word[i:i + 2] for i in range(max(1, len(word) - 1)))
        else:
            tokens.append(word)
    return " ".join(tokens)


def expand_query(query: str) -> str:
    if not get_settings().knowledge_query_expansion:
        return query
    groups = (("单点登录", "SSO"), ("组织架构", "部门层级"),
              ("字段映射", "列映射"), ("上线验收", "验收标准"))
    additions: list[str] = []
    for group in groups:
        if any(re.search(r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z0-9_])", query, re.I)
               if term.isascii() else term in query for term in group):
            additions.extend(term for term in group if term.lower() not in query.lower())
    return query + ("\n相关术语：" + "、".join(additions) if additions else "")


async def document_chunks(body):
    s = get_settings()
    if s.rag_mode == "real":
        from backend.retrieval_models import chunks
        return await chunks(body, s.knowledge_chunk_tokens, s.knowledge_chunk_overlap)
    return chunk_text(body, s.knowledge_chunk_tokens, s.knowledge_chunk_overlap)


def index_identity() -> str:
    s = get_settings()
    if s.rag_mode == "real":
        model = s.embedding_local_model if s.embedding_mode == "local" else s.embedding_model
        signature = hashlib.sha256(json.dumps([model, s.embedding_mode, s.embedding_revision,
            s.embedding_dimensions, s.embedding_base_url, s.embedding_query_instruction,
            "l2-normalized", s.embedding_query_style, s.embedding_max_length,
            s.knowledge_chunk_tokens, s.knowledge_chunk_overlap, "structure-v3-tokenizer",
            s.knowledge_index_version]).encode()).hexdigest()[:24]
        return f"real:{s.knowledge_index_version}:{signature}"
    signature = hashlib.sha256(f"{s.embedding_model}|{s.embedding_dimensions}|{s.embedding_base_url or s.model_base_url}".encode()).hexdigest()[:16]
    return f"{s.knowledge_index_version}:{signature}"


async def index_document(session, document: KnowledgeDocument) -> None:
    identity = index_identity()
    s = get_settings()
    model_name = s.embedding_local_model if s.rag_mode == "real" and s.embedding_mode == "local" else s.embedding_model
    pieces = []
    chunks = await document_chunks(document.body)
    vectors: list[list[float] | None]
    if s.rag_mode == "real":
        from backend.retrieval_models import embeddings
        # Yield between batches so interactive queries can acquire the model lock.
        vectors = []
        for start in range(0, len(chunks), s.embedding_batch_size):
            vectors.extend(await embeddings([body for _, body in chunks[start:start + s.embedding_batch_size]]))
    else:
        vectors = [await embed(body) for _, body in chunks]
    for ordinal, ((heading, body), vector) in enumerate(zip(chunks, vectors, strict=True)):
        pieces.append(KnowledgePiece(tenant_id=document.tenant_id, document_id=document.id,
            ordinal=ordinal, heading=heading, body=body, lexemes=lexemes(document.title + " " + heading + " " + body),
            index_version=identity, embedding_model=model_name, embedding=vector))
    # Publish the new index atomically only when every embedding succeeded.
    await session.execute(delete(KnowledgePiece).where(KnowledgePiece.document_id == document.id, KnowledgePiece.index_version == identity))
    session.add_all(pieces)
    await session.flush()
    if get_settings().rag_mode == "real":
        from backend.lexical_index import publish
        await publish(document, pieces)
    document.index_version, document.index_status, document.index_error = identity, "ready", ""
    await session.flush()


async def process_pending(session, tenant_id: str, limit: int = 10) -> int:
    docs = (await session.scalars(select(KnowledgeDocument).where(
        KnowledgeDocument.tenant_id == tenant_id, KnowledgeDocument.active.is_(True),
        KnowledgeDocument.index_status.in_(["pending", "failed"]), KnowledgeDocument.index_attempts < 3,
    ).limit(limit).with_for_update(skip_locked=True))).all()
    for doc in docs:
        try:
            async with session.begin_nested():
                async with asyncio.timeout(180):
                    await index_document(session, doc)
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError) as exc:
            await session.refresh(doc)
            doc.index_attempts += 1
            doc.index_status, doc.index_error = "failed", type(exc).__name__
        except Exception as exc:
            from fastapi import HTTPException
            if not isinstance(exc, HTTPException):
                raise
            await session.refresh(doc)
            doc.index_attempts += 1
            doc.index_status, doc.index_error = "failed", f"HTTP{exc.status_code}"
    await session.flush()
    return len(docs)


async def rerank(query: str, hits: list[dict]) -> list[dict]:
    if get_settings().rag_mode != "real":
        return hits
    from backend.retrieval_models import rank
    return await rank(query, hits)


async def retrieve(session, tenant_id: str, query: str, limit: int = 5, *, project_ids: list[str] | None = None, knowledge_only: bool = False) -> list[dict]:
    if get_settings().rag_mode == "real":
        from backend.retrieval_sources import retrieve as retrieve_sources
        return await retrieve_sources(session, tenant_id, query, limit, project_ids=project_ids or [], knowledge_only=knowledge_only)
    if not query.strip():
        return []
    limit = min(max(limit, 1), 5)
    latest = aliased(KnowledgeDocument)
    valid = [KnowledgeDocument.tenant_id == tenant_id, KnowledgeDocument.active.is_(True),
        or_(KnowledgeDocument.project_id.is_(None), KnowledgeDocument.project_id.in_(project_ids or [])),
        KnowledgeDocument.index_status == "ready", KnowledgeDocument.index_version == index_identity(),
        ~exists(select(latest.id).where(latest.tenant_id == tenant_id,
            latest.index_status.not_like('v3_%'),
            latest.title == KnowledgeDocument.title, latest.version > KnowledgeDocument.version)),
        KnowledgePiece.tenant_id == tenant_id, KnowledgePiece.index_version == KnowledgeDocument.index_version]
    base = select(KnowledgePiece, KnowledgeDocument).join(KnowledgeDocument, KnowledgePiece.document_id == KnowledgeDocument.id).where(*valid)
    rankings: list[list[str]] = []
    rows = {}
    retrieval_query = expand_query(query)
    terms = lexemes(retrieval_query).split()
    if not terms:
        return []
    if session.bind.dialect.name == "postgresql":
        # SQL visibility and version constraints apply before either LIMIT.
        lexical = base.where(text("to_tsvector('simple', knowledge_chunks.lexemes) @@ to_tsquery('simple', :terms)")).order_by(
            text("ts_rank(to_tsvector('simple', knowledge_chunks.lexemes), to_tsquery('simple', :terms)) DESC"), KnowledgePiece.id).limit(30)
        if get_settings().rag_mode == "real":
            from backend.lexical_index import search as bm25
            identifiers = await bm25(tenant_id, project_ids, index_identity(), " ".join(terms))
            authorized = (await session.execute(base.where(KnowledgePiece.id.in_(identifiers)))).all()
            by_id = {c.id: (c, d) for c, d in authorized}
            found = [by_id[i] for i in identifiers if i in by_id]
        else:
            found = (await session.execute(lexical, {"terms": " | ".join("'" + t.replace("'", "") + "'" for t in terms)})).all()
        rows.update({c.id: (c, d) for c, d in found})
        rankings.append([c.id for c, _ in found])
        vector = await embed(retrieval_query, query=True) if get_settings().rag_mode == "real" else await embed(retrieval_query)
        if vector is not None:
            s = get_settings()
            model_name = s.embedding_local_model if s.rag_mode == "real" and s.embedding_mode == "local" else s.embedding_model
            await session.execute(text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))
            found = (await session.execute(base.where(KnowledgePiece.embedding_model == model_name,
                KnowledgePiece.embedding.is_not(None), text("(knowledge_chunks.embedding <=> CAST(:vector AS vector)) <= :distance")).order_by(text("knowledge_chunks.embedding <=> CAST(:vector AS vector)"), KnowledgePiece.id).limit(30), {"vector": json.dumps(vector), "distance": 1 - get_settings().knowledge_min_similarity})).all()
            rows.update({c.id: (c, d) for c, d in found})
            rankings.append([c.id for c, _ in found])
    else:
        if get_settings().rag_mode == "real":
            from fastapi import HTTPException
            raise HTTPException(503, "真实持久化向量检索需要 PostgreSQL/pgvector；当前 SQLite 仅用于离线测试")
        found = (await session.execute(base)).all()
        tokens = set(terms)
        found = [(c, d) for c, d in found if tokens & set(c.lexemes.split())]
        found.sort(key=lambda pair: (-len(tokens & set(pair[0].lexemes.split())), pair[0].id))
        rows = {c.id: (c, d) for c, d in found[:30]}
        rankings.append(list(rows))
    if not rows:
        any_doc = await session.scalar(select(KnowledgeDocument.id).where(KnowledgeDocument.tenant_id == tenant_id).limit(1))
        return search(query, limit=limit) if not knowledge_only and not any_doc and get_settings().model_mode == "deterministic" else []
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, identifier in enumerate(ranking, 1):
            scores[identifier] = scores.get(identifier, 0) + 1 / (60 + rank)
    trace_id = str(uuid4())
    hits = []
    s = get_settings()
    candidates = min(s.reranker_max_candidates, s.reranker_max_windows)
    for identifier in sorted(scores, key=lambda key: (-scores[key], key))[:candidates]:
        c, d = rows[identifier]
        hits.append({"id": c.id, "document_id": d.id, "title": d.title, "module": d.module,
            "text": c.body, "version": d.version, "source": d.source, "heading": c.heading,
            "ordinal": c.ordinal, "score": scores[identifier], "index_version": c.index_version, "trace_id": trace_id})
    ordered = await rerank(query, hits)
    selected, seen = [], set()
    for hit in ordered:
        key = re.sub(r"\s+", "", hit["text"])
        if key not in seen:
            seen.add(key)
            selected.append(hit)
        if len(selected) >= limit:
            break
    return selected
