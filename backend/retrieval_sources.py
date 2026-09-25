"""Transactional source registration, bounded indexing and current-source validation."""
import asyncio
import hashlib
import json
import re
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, delete, or_, select

from backend.chat_models import ChatConversation
from backend.config import get_settings
from backend.knowledge import document_chunks, expand_query, index_identity, lexemes
from backend.models import KnowledgeDocument, Project, ProjectArtifact, ProjectDocument
from backend.retrieval_sources_models import RetrievalChunk, RetrievalSource


def digest(title, body):
    return hashlib.sha256((title + "\n" + body).encode()).hexdigest()


def status(source):
    return {"source_id": source.id, "index_status": source.phase, "index_generation": source.generation,
            "index_progress": {"done": source.chunks_done, "total": source.chunks_total},
            "index_error": source.error_code, "retryable": source.active and source.phase == "failed"}


async def live_status(source):
    from backend.retrieval_progress import read

    result = status(source)
    progress = await read(source)
    if progress and progress.get("phase") in {"parsing", "indexing"}:
        result.update(index_status=progress["phase"],
                      index_progress={"done": progress["done"], "total": progress["total"]},
                      retryable=False, index_error="")
    return result


async def register(session, *, tenant_id, kind, origin_id, title, body, version="1",
                   project_id=None, conversation_id=None, owner_id=None, active=True):
    source = await session.scalar(select(RetrievalSource).where(RetrievalSource.tenant_id == tenant_id,
        RetrievalSource.kind == kind, RetrievalSource.origin_id == origin_id).with_for_update())
    fingerprint = digest(title, body)
    if source is None:
        source = RetrievalSource(tenant_id=tenant_id, kind=kind, origin_id=origin_id,
            title=title, body=body, version=str(version), digest=fingerprint,
            project_id=project_id, conversation_id=conversation_id, owner_id=owner_id, active=active)
        session.add(source)
    elif (source.digest, source.version, source.active, source.project_id, source.conversation_id, source.owner_id) != (
            fingerprint, str(version), active, project_id, conversation_id, owner_id):
        source.title, source.body, source.digest, source.version = title, body, fingerprint, str(version)
        source.project_id, source.conversation_id, source.owner_id = project_id, conversation_id, owner_id
        source.active = active
        source.generation += 1
        source.phase, source.attempts, source.error_code = "pending", 0, ""
        source.chunks_done, source.chunks_total = 0, 0
    elif source.index_identity and source.index_identity != index_identity():
        source.phase, source.attempts = "pending", 0
        source.generation += 1
    await session.flush()
    return source


async def sync_knowledge(session, document):
    if document.index_status.startswith('v3_'):
        return None
    return await register(session, tenant_id=document.tenant_id, kind="knowledge", origin_id=document.id,
        title=document.title, body=document.body, version=document.version,
        project_id=document.project_id, active=document.active)


async def sync_project(session, project):
    material = await session.scalar(select(ProjectDocument).where(ProjectDocument.project_id == project.id))
    body = project.requirements_text
    if material:
        body += "\n\n实施材料\n" + json.dumps(material.content, ensure_ascii=False, sort_keys=True)
    return await register(session, tenant_id=project.tenant_id, kind="project", origin_id=project.id,
        title=project.name + " · 需求与实施材料", body=body, project_id=project.id,
        active=project.deleted_at is None)


async def sync_artifact(session, artifact):
    return await register(session, tenant_id=artifact.tenant_id, kind="artifact", origin_id=artifact.id,
        title=artifact.title, body=artifact.content, version=artifact.version, project_id=artifact.project_id)


async def sync_conversation(session, conversation, documents=None):
    documents = conversation.documents if documents is None else documents
    identifiers = {doc["id"] for doc in documents}
    previous = (await session.scalars(select(RetrievalSource).where(
        RetrievalSource.tenant_id == conversation.tenant_id,
        RetrievalSource.conversation_id == conversation.id))).all()
    for source in previous:
        if source.origin_id not in identifiers:
            source.active, source.phase = False, "pending"
    for doc in documents:
        await register(session, tenant_id=conversation.tenant_id, kind="conversation", origin_id=doc["id"],
            title=doc["name"], body=doc["text"], conversation_id=conversation.id,
            project_id=conversation.project_id, owner_id=conversation.user_id)


async def backfill(session, tenant_id):
    """Explicit, idempotent backfill of database-owned sources only."""
    count = 0
    for model, sync in ((KnowledgeDocument, sync_knowledge), (Project, sync_project),
                        (ProjectArtifact, sync_artifact), (ChatConversation, sync_conversation)):
        for item in await session.scalars(select(model).where(model.tenant_id == tenant_id)):
            await sync(session, item)
            count += 1
    return count


async def current(session, source):
    """Revalidate origin existence/content/version even if hooks or search are stale."""
    if source.kind == "knowledge":
        document = await session.get(KnowledgeDocument, source.origin_id)
        if not document or document.tenant_id != source.tenant_id or not document.active or document.project_id != source.project_id:
            return False
        if document.index_status.startswith('v3_'):
            return False
        newer = await session.scalar(select(KnowledgeDocument.id).where(
            KnowledgeDocument.tenant_id == source.tenant_id, KnowledgeDocument.title == document.title,
            KnowledgeDocument.index_status.not_like('v3_%'),
            KnowledgeDocument.version > document.version).limit(1))
        return not newer and source.version == str(document.version) and source.digest == digest(document.title, document.body)
    if source.kind == "conversation":
        conversation = await session.get(ChatConversation, source.conversation_id)
        if not conversation or (conversation.tenant_id, conversation.user_id, conversation.project_id) != (
                source.tenant_id, source.owner_id, source.project_id):
            return False
        return any(doc["id"] == source.origin_id and source.digest == digest(doc["name"], doc["text"])
                   for doc in conversation.documents)
    project = await session.get(Project, source.project_id)
    if not project or project.tenant_id != source.tenant_id or project.deleted_at:
        return False
    if source.kind == "project":
        material = await session.scalar(select(ProjectDocument).where(ProjectDocument.project_id == project.id))
        body = project.requirements_text
        if material:
            body += "\n\n实施材料\n" + json.dumps(material.content, ensure_ascii=False, sort_keys=True)
        return source.digest == digest(project.name + " · 需求与实施材料", body)
    if source.kind == "artifact":
        artifact = await session.get(ProjectArtifact, source.origin_id)
        if not artifact or artifact.project_id != project.id:
            return False
        latest = await session.scalar(select(ProjectArtifact.id).where(ProjectArtifact.project_id == project.id,
            ProjectArtifact.kind == artifact.kind).order_by(ProjectArtifact.created_at.desc(), ProjectArtifact.id.desc()).limit(1))
        return latest == artifact.id and source.digest == digest(artifact.title, artifact.content)
    return False


async def authorized_sources(session, tenant_id, *, project_ids=(), user_id=None,
                             conversation_id=None, artifact_project_ids=()):
    scopes = [and_(RetrievalSource.kind == "knowledge", or_(RetrievalSource.project_id.is_(None),
               RetrievalSource.project_id.in_(project_ids))),
              and_(RetrievalSource.kind == "project", RetrievalSource.project_id.in_(project_ids)),
              and_(RetrievalSource.kind == "artifact", RetrievalSource.project_id.in_(artifact_project_ids))]
    if user_id and conversation_id:
        scopes.append(and_(RetrievalSource.kind == "conversation", RetrievalSource.owner_id == user_id,
            RetrievalSource.conversation_id == conversation_id,
            or_(RetrievalSource.project_id.is_(None), RetrievalSource.project_id.in_(project_ids))))
    rows = (await session.scalars(select(RetrievalSource).where(RetrievalSource.tenant_id == tenant_id,
        RetrievalSource.active.is_(True), or_(*scopes)))).all()
    return [source for source in rows if await current(session, source)]


async def index_source(session, source):
    from backend.retrieval_models import embeddings
    from backend.retrieval_progress import report
    from backend.source_search import publish
    s = get_settings()
    if not source.active or not await current(session, source):
        source.active, source.phase = False, "inactive"
        await session.execute(delete(RetrievalChunk).where(RetrievalChunk.source_id == source.id))
        if s.rag_mode == "real":
            from backend.source_search import remove
            await remove(source.id)
        return
    source.phase = "parsing"
    await report(source)
    chunks = await document_chunks(source.body)
    source.phase, source.chunks_total, source.chunks_done = "indexing", len(chunks), 0
    await report(source)
    pieces = []
    for start in range(0, len(chunks), s.embedding_batch_size):
        batch = chunks[start:start + s.embedding_batch_size]
        vectors = await embeddings([body for _, body in batch]) if s.rag_mode == "real" else [None] * len(batch)
        for offset, ((heading, body), vector) in enumerate(zip(batch, vectors, strict=True)):
            ordinal = start + offset
            citation = f"{source.origin_id}:{ordinal}"
            if source.kind == "knowledge":
                from backend.agent_models import KnowledgePiece
                old = await session.scalar(select(KnowledgePiece.id).where(
                    KnowledgePiece.document_id == source.origin_id, KnowledgePiece.body == body).limit(1))
                citation = old or citation
            pieces.append(RetrievalChunk(id=str(uuid4()), tenant_id=source.tenant_id, source_id=source.id,
                generation=source.generation, ordinal=ordinal, citation_id=citation, heading=heading,
                page=int(match[1]) if (match := re.match(r"第 (\d+) 页", heading)) else None,
                body=body, lexemes=lexemes(source.title + " " + heading + " " + body), embedding=vector))
        source.chunks_done += len(batch)
        await report(source)
    await session.execute(delete(RetrievalChunk).where(RetrievalChunk.source_id == source.id))
    session.add_all(pieces)
    await session.flush()
    if s.rag_mode == "real":
        await publish(source, pieces)
    source.phase, source.index_identity = "ready", index_identity()
    source.indexed_generation, source.error_code = source.generation, ""
    if source.kind == "knowledge":
        document = await session.get(KnowledgeDocument, source.origin_id)
        document.index_status, document.index_version, document.index_error = "ready", index_identity(), ""


async def process_sources(session, tenant_id, limit=10):
    # Rotate through published sources so deletion/revocation is also cleaned
    # when an older business path did not register an explicit indexing task.
    from backend.models import utcnow

    published = (await session.scalars(select(RetrievalSource).where(
        RetrievalSource.tenant_id == tenant_id, RetrievalSource.phase == "ready")
        .order_by(RetrievalSource.updated_at).limit(limit).with_for_update(skip_locked=True))).all()
    for source in published:
        if not source.active or not await current(session, source):
            source.phase, source.active, source.attempts = "pending", False, 0
        elif source.index_identity != index_identity():
            # Deployment/model changes must not leave ready-but-unsearchable
            # sources stranded forever. Rebuild through the normal atomic path.
            source.phase, source.attempts, source.error_code = "pending", 0, ""
            source.generation += 1
            source.chunks_done, source.chunks_total = 0, 0
        source.updated_at = utcnow()
    rows = (await session.scalars(select(RetrievalSource).where(RetrievalSource.tenant_id == tenant_id,
        RetrievalSource.phase.in_(["pending", "failed"]), RetrievalSource.attempts < 3)
        .order_by(RetrievalSource.updated_at).limit(limit).with_for_update(skip_locked=True))).all()
    for source in rows:
        try:
            async with session.begin_nested():
                async with asyncio.timeout(180):
                    await index_source(session, source)
        except Exception as exc:
            await session.refresh(source)
            source.attempts += 1
            source.phase = "failed"
            source.error_code = "MODEL_BUSY" if isinstance(exc, HTTPException) and isinstance(exc.detail, dict) and exc.detail.get("code") == "MODEL_BUSY" else "INDEX_DEPENDENCY_FAILED"
            if isinstance(exc, HTTPException) and exc.status_code in {413, 415, 422}:
                source.attempts = 3
    await session.flush()
    return len(rows)


async def retrieve(session, tenant_id, query, limit=5, *, knowledge_only=False,
                   include_knowledge=True, inspection_candidates=False, **scope):
    from sqlalchemy import text

    from backend.retrieval_models import embeddings, rank
    from backend.source_search import search
    if not query.strip():
        return []
    s = get_settings()
    sources = await authorized_sources(session, tenant_id, **scope)
    if knowledge_only:
        sources = [source for source in sources if source.kind == "knowledge"]
    elif not include_knowledge:
        sources = [source for source in sources if source.kind != "knowledge"]
    ready = {source.id: source for source in sources if source.phase == "ready"
             and source.index_identity == index_identity() and source.indexed_generation == source.generation}
    if not ready:
        return []
    base = select(RetrievalChunk).join(RetrievalSource).where(RetrievalChunk.tenant_id == tenant_id,
        RetrievalChunk.source_id.in_(ready), RetrievalChunk.generation == RetrievalSource.generation)
    expanded = expand_query(query)
    rows, rankings = {}, []
    if s.rag_mode == "real":
        if session.bind.dialect.name != "postgresql":
            raise HTTPException(503, "真实检索需要 PostgreSQL/pgvector")
        identifiers = await search(tenant_id, list(ready), lexemes(expanded))
        lexical = (await session.scalars(base.where(RetrievalChunk.id.in_(identifiers)))).all()
        rows.update({chunk.id: chunk for chunk in lexical})
        rankings.append([identifier for identifier in identifiers if identifier in rows])
        vector = (await embeddings([expanded], query=True))[0]
        await session.execute(text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))
        semantic = (await session.scalars(base.where(RetrievalChunk.embedding.is_not(None),
            text("(retrieval_source_chunks.embedding <=> CAST(:vector AS vector)) <= :distance"))
            .order_by(text("retrieval_source_chunks.embedding <=> CAST(:vector AS vector)"), RetrievalChunk.id)
            .limit(30), {"vector": json.dumps(vector), "distance": 1 - s.knowledge_min_similarity})).all()
        rows.update({chunk.id: chunk for chunk in semantic})
        rankings.append([chunk.id for chunk in semantic])
    else:
        tokens = set(lexemes(expanded).split())
        lexical = [chunk for chunk in await session.scalars(base) if tokens & set(chunk.lexemes.split())]
        lexical.sort(key=lambda chunk: (-len(tokens & set(chunk.lexemes.split())), chunk.id))
        rows = {chunk.id: chunk for chunk in lexical[:30]}
        rankings = [list(rows)]
    scores = {}
    for ranking in rankings:
        for position, identifier in enumerate(ranking, 1):
            scores[identifier] = scores.get(identifier, 0) + 1 / (60 + position)
    hits = []
    for identifier in sorted(scores, key=lambda value: (-scores[value], value))[:min(s.reranker_max_candidates, s.reranker_max_windows)]:
        chunk = rows[identifier]
        source = ready[chunk.source_id]
        hits.append({"id": chunk.citation_id, "source_id": source.id, "document_id": source.origin_id,
            "title": source.title, "text": chunk.body, "heading": chunk.heading, "page": chunk.page,
            "version": source.version, "source": {"knowledge": "企业知识库", "conversation": "私有会话附件",
            "project": "项目材料", "artifact": "项目交付物"}[source.kind], "kind": "customer_document",
            "score": scores[identifier], "index_version": source.index_identity})
        if inspection_candidates:
            hits[-1].update(chunk_id=chunk.id, ordinal=chunk.ordinal, generation=chunk.generation)
    if inspection_candidates:
        return await rank(query, hits, with_scores=True)
    ordered = await rank(query, hits) if s.rag_mode == "real" else hits
    output, seen = [], set()
    for hit in ordered:
        if hit["text"] not in seen:
            seen.add(hit["text"])
            output.append(hit)
        if len(output) >= min(limit, 5):
            break
    return output
