"""Versioned side-index lifecycle and bounded structure-preserving units."""

import asyncio
import hashlib
import json
import re
import time
from dataclasses import asdict

from fastapi import HTTPException
from sqlalchemy import delete, select

from backend.config import get_settings
from backend.document_parse_process import parse_isolated
from backend.document_parser import Node, parse
from backend.knowledge import lexemes
from backend.knowledge_index_models import DocumentNode, IndexedDocument, RetrievalUnit
from backend.models import KnowledgeDocument
from backend.retrieval_runtime import embeddings
from backend.retrieval_runtime import token_counts as model_counts


async def token_counts(texts):
    values = []
    for start in range(0, len(texts), 64):
        values.extend(await model_counts(texts[start : start + 64]))
    return values


def identity():
    from backend.retrieval_release import model_identity
    from backend.retrieval_runtime import settings

    s = settings()
    spec = {
        **model_identity(),
        "query_style": s.embedding_query_style,
        "instruction": s.embedding_query_instruction,
        "max_length": s.embedding_max_length,
    }
    return (
        # Stored format identity remains stable across module-name changes.
        "v3-structure-1:"
        + hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:32]
    )


def index_state(source, *, current_identity=None):
    """Report the same readiness barrier used by retrieval, not just stored phase."""
    if source is None:
        return "missing"
    if source.phase == "ready" and source.identity != (current_identity or identity()):
        return "stale"
    return source.phase


def exact_terms(text):
    return sorted(set(re.findall(r"\$\[[^\n]+|[A-Za-z_][\w./:-]*|\d+(?:[.-]\d+)+", text)))


async def register(session, document, filename=None, raw=None, options=None):
    source = await session.scalar(
        select(IndexedDocument).where(
            IndexedDocument.origin_id == document.id, IndexedDocument.tenant_id == document.tenant_id
        )
    )
    if source:
        return source
    if (filename or document.source or "").lower().endswith((".doc", ".ppt", ".xls")):
        return None
    raw = raw if raw is not None else document.body.encode("utf-8")
    source = IndexedDocument(
        tenant_id=document.tenant_id,
        origin_id=document.id,
        filename=filename or "document.md",
        raw=raw,
        digest=hashlib.sha256(raw).hexdigest(),
        options=options or {},
    )
    session.add(source)
    await session.flush()
    return source


async def split_node(node):
    if node.kind in {"heading", "object", "array", "unparsed_html"} or not node.text.strip():
        return []
    prefix = (node.context + "\n") if node.context else ""
    text = prefix + node.text
    if (await token_counts([text]))[0] <= (384 if node.kind == "table_record" else 300):
        return [text]
    if node.kind in {"code", "table"}:
        pieces = text.splitlines(keepends=True)
        if node.kind == "table" and len(pieces) > 2:
            header = "".join(pieces[:2])
            pieces = [header + p for p in pieces[2:]]
    else:
        pieces = re.split(r"(?<=[。！？.!?;；])(?=\s|[\u4e00-\u9fff])|\n\s*\n", node.text)
        pieces = [prefix + p for p in pieces if p.strip()]
    # Oversized atoms are split into original character slices, never tokenizer decoding.
    # Marked continuation parts remain one node so query-time supplementation can reconnect them.
    bounded = []
    for piece in pieces:
        while (await token_counts([piece]))[0] > 384:
            node.location["requires_all_parts"] = True
            lo, hi = 1, len(piece)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if (await token_counts([piece[:mid]]))[0] <= 300:
                    lo = mid
                else:
                    hi = mid - 1
            bounded.append(piece[:lo])
            piece = piece[lo:]
        if piece:
            bounded.append(piece)
    pieces = bounded
    output = []
    current = ""
    for piece in pieces:
        joined = current + ("\n" if current else "") + piece
        if current and (await token_counts([joined]))[0] > 300:
            output.append(current)
            current = piece
        else:
            current = joined
    if current:
        output.append(current)
    return output


async def index_document(session, source):
    from backend.knowledge_search import publish

    started = time.perf_counter()
    parser = parse_isolated if source.filename.lower().endswith((".pdf", ".docx", ".pptx", ".xlsx")) else parse
    parsed = await asyncio.to_thread(parser, source.filename, source.raw, **source.options)
    parsed_at = time.perf_counter()
    origin = await session.get(KnowledgeDocument, source.origin_id)
    if not origin or not origin.active:
        source.phase = "inactive"
        return
    nodes = []
    units = []
    table_records = {}
    for node in parsed.nodes:
        if node.kind == "table_cell":
            table_records.setdefault(node.record, []).append(node)
    packed = {}
    skipped = set()
    for record, cells in table_records.items():
        text = "\n".join((c.context + "\n" if c.context else "") + c.text for c in cells)
        if (await token_counts([text]))[0] <= 384:
            first = cells[0]
            packed[first.id] = Node(
                "record-" + first.id,
                "table_record",
                text,
                {
                    **first.location,
                    "columns": [c.location["column"] for c in cells],
                    "cell_nodes": [c.id for c in cells],
                },
                parent=first.parent,
                heading=first.heading,
                record=record,
            )
            skipped.update(c.id for c in cells)
    index_nodes = []
    for node in parsed.nodes:
        # Retain original structural cells, even when their complete row is indexed together.
        nodes.append(
            DocumentNode(
                id=source.id + ":" + node.id,
                tenant_id=source.tenant_id,
                document_id=source.id,
                structure=asdict(node),
            )
        )
        if node.id in packed:
            row = packed[node.id]
            index_nodes.append(row)
            nodes.append(
                DocumentNode(
                    id=source.id + ":" + row.id,
                    tenant_id=source.tenant_id,
                    document_id=source.id,
                    structure=asdict(row),
                )
            )
        elif node.id not in skipped:
            index_nodes.append(node)
    for node in index_nodes:
        node_id = source.id + ":" + node.id
        pieces = await split_node(node)
        for part, text in enumerate(pieces):
            count = (await token_counts([text]))[0]
            units.append(
                RetrievalUnit(
                    id=node_id + ":" + str(part),
                    tenant_id=source.tenant_id,
                    document_id=source.id,
                    node_id=node_id,
                    ordinal=len(units),
                    text=text,
                    heading=node.heading,
                    kind=node.kind,
                    location={**node.location, "part": part, "parts": len(pieces), "node": node.id},
                    parent=node.parent,
                    record=node.record,
                    tokens=count,
                    lexemes=lexemes(origin.title + " " + node.heading + " " + text),
                    identifiers=exact_terms(text),
                )
            )
    from backend.evidence_context import identity_contexts

    contexts = identity_contexts(index_nodes, parsed.nodes)
    for stored in nodes:
        refs = contexts.get(stored.structure["id"])
        if refs:
            stored.structure["identity_nodes"] = [source.id + ":" + ref for ref in refs]
    if not units:
        raise HTTPException(422, "无可索引的正文单元")
    def same_source_position(left, right):
        for key in ("page", "slide", "sheet"):
            if key in left.location or key in right.location:
                return left.location.get(key) == right.location.get(key)
        return True

    for i, unit in enumerate(units):
        unit.previous_id = units[i - 1].id if i and same_source_position(units[i - 1], unit) else None
        unit.next_id = units[i + 1].id if i + 1 < len(units) and same_source_position(unit, units[i + 1]) else None
    for start in range(0, len(units), 16):
        batch = units[start : start + 16]
        vectors = (
            await embeddings([u.text for u in batch])
            if get_settings().rag_mode == "real"
            else [None] * len(batch)
        )
        for u, v in zip(batch, vectors, strict=True):
            u.embedding = v
    await session.execute(delete(RetrievalUnit).where(RetrievalUnit.document_id == source.id))
    await session.execute(delete(DocumentNode).where(DocumentNode.document_id == source.id))
    session.add_all(nodes)
    session.add_all(units)
    await session.flush()
    if get_settings().rag_mode == "real":
        await publish(source, units)
    source.phase = "ready"
    source.identity = identity()
    source.warnings = parsed.warnings
    source.error = ""
    source.metrics = {
        "parse_ms": round((parsed_at - started) * 1000, 2),
        "index_ms": round((time.perf_counter() - parsed_at) * 1000, 2),
        "nodes": len(nodes),
        "units": len(units),
        "format": parsed.format,
        "ocr_pages": parsed.ocr_pages,
    }
    # Uploaded file origins remain owned by V3; do not rebuild the old index.
    if origin.index_status.startswith("indexed_"):
        displayed = []
        last_location = None
        for node in parsed.nodes:
            if not node.text:
                continue
            location = node.location
            marker = ("page", location["page"]) if "page" in location else (
                ("slide", location["slide"]) if "slide" in location else (
                    ("sheet", location["sheet"]) if "sheet" in location else None))
            if marker and marker != last_location:
                label = (f"第 {marker[1]} 页" if marker[0] == "page" else
                         f"第 {marker[1]} 张" if marker[0] == "slide" else
                         f"工作表 {marker[1]}")
                displayed.append("## " + label)
                last_location = marker
            displayed.append((node.context + "\n" if node.context else "") + node.text)
        origin.body = "\n\n".join(displayed)
        origin.index_status = "indexed_ready"
        origin.index_error = ""


async def process(session, tenant_id, limit=4):
    # Bootstrap historical uploads, not just documents uploaded after Knowledge enabled.
    missing = list(
        await session.scalars(
            select(KnowledgeDocument)
            .where(
                KnowledgeDocument.tenant_id == tenant_id,
                KnowledgeDocument.active.is_(True),
                ~select(IndexedDocument.id)
                .where(
                    IndexedDocument.origin_id == KnowledgeDocument.id, IndexedDocument.tenant_id == tenant_id
                )
                .exists(),
                KnowledgeDocument.body != "",
                ~KnowledgeDocument.source.ilike("%.pdf"),
                ~KnowledgeDocument.source.ilike("%.pptx"),
                ~KnowledgeDocument.source.ilike("%.xlsx"),
                ~KnowledgeDocument.source.ilike("%.doc"),
                ~KnowledgeDocument.source.ilike("%.ppt"),
                ~KnowledgeDocument.source.ilike("%.xls"),
            )
            .order_by(KnowledgeDocument.created_at)
            .limit(4)
            .with_for_update(skip_locked=True)
        )
    )
    for document in missing:
        await register(session, document)
    stale = list(
        await session.scalars(
            select(IndexedDocument)
            .where(
                IndexedDocument.tenant_id == tenant_id,
                IndexedDocument.phase == "ready",
                IndexedDocument.identity != identity(),
            )
            .limit(4)
            .with_for_update(skip_locked=True)
        )
    )
    for source in stale:
        source.phase, source.attempts, source.error = "pending", 0, ""
    await session.flush()
    rows = list(
        await session.scalars(
            select(IndexedDocument)
            .where(
                IndexedDocument.tenant_id == tenant_id,
                IndexedDocument.phase.in_(["pending", "failed"]),
                IndexedDocument.attempts < 3,
                IndexedDocument.next_attempt <= time.time(),
            )
            .order_by(IndexedDocument.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    for source in rows:
        try:
            async with session.begin_nested():
                async with asyncio.timeout(810 if source.filename.lower().endswith(".pdf") else 180):
                    await index_document(session, source)
        except Exception as exc:
            await session.refresh(source)
            source.attempts += 3 if isinstance(exc, HTTPException) and exc.status_code in (413, 415, 422) else 1
            source.phase = "failed"
            source.next_attempt = time.time() + min(3600, 30 * 2 ** source.attempts)
            source.error = (
                exc.detail[:300]
                if isinstance(exc, HTTPException) and isinstance(exc.detail, str)
                else type(exc).__name__
            )
            origin = await session.get(KnowledgeDocument, source.origin_id)
            if origin and origin.index_status.startswith("indexed_"):
                origin.index_status = "indexed_failed"
                origin.index_error = source.error
