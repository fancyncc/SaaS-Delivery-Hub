"""Extractive evidence selection used exclusively by the inspection endpoint."""

import json
import math
import re
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select

from backend.config import get_settings
from backend.knowledge import index_identity, lexemes
from backend.retrieval_models import rank, token_counts
from backend.retrieval_sources import authorized_sources, retrieve
from backend.retrieval_sources_models import RetrievalChunk


def model_identity():
    s = get_settings()
    return {
        "model": s.reranker_local_model if s.reranker_mode == "local" else s.reranker_model,
        "revision": s.reranker_revision,
        "max_length": s.reranker_max_length,
        "max_windows": s.reranker_max_windows,
        "index_identity": index_identity(),
        "pipeline_version": "inspection-v2.1",
    }


def calibrated_threshold():
    path = Path(get_settings().rag_inspection_calibration)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[1] / path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        value = data["threshold"]
        if (
            data["identity"] != model_identity()
            or data.get("passed") is not True
            or type(value) not in (float, int)
            or not math.isfinite(value)
        ):
            raise ValueError("calibration")
        return value
    except (OSError, ValueError, KeyError, TypeError):
        raise HTTPException(503, "查验新流程尚未通过当前模型版本的校准验收") from None


def serialize_evidence(hits):
    # This exact string, not diagnostic JSON, is the future context boundary.
    return "\n\n".join(
        f"[{h['id']}] {h['title']}\n{h.get('heading', '')}\n{h['text']}" for h in hits
    )


def units(text):
    """Whole paragraphs; tables split by rows with repeated column headers."""
    output = []
    for paragraph in re.split(r"\n\s*\n", text.strip()):
        lines = paragraph.splitlines()
        if (
            len(lines) >= 3
            and lines[0].lstrip().startswith("|")
            and re.fullmatch(r"[\s|:\-]+", lines[1])
        ):
            output.extend("\n".join([*lines[:2], row]) for row in lines[2:])
        else:
            output.append(paragraph)
    return [p for p in output if p.strip()]


def deduplicate(hits):
    output, seen, removed = [], set(), []
    for hit in hits:
        body = hit["text"]
        if body in seen:
            removed.append(hit["id"])
            continue
        seen.add(body)
        for earlier in output:
            if earlier["document_id"] != hit["document_id"] or earlier.get("generation") != hit.get(
                "generation"
            ):
                continue
            if earlier.get("ordinal", -2) + 1 != hit.get("ordinal", -1):
                continue
            for size in range(min(len(earlier["text"]), len(body)), 19, -1):
                if earlier["text"].endswith(body[:size]):
                    body = body[size:].lstrip()
                    break
        if body:
            output.append({**hit, "text": body})
        else:
            removed.append(hit["id"])
    return output, removed


async def supplement(session, tenant_id, query, project_ids, selected, existing):
    enumeration = bool(re.search(r"字段|属性|完整清单|全部.*(?:列表|清单)|列出所有", query))
    references = re.findall(
        r"(?:参见|见|依据|遵循)\s*([A-Za-z]+-\d+|[\u4e00-\u9fff]{2,12}章节)",
        "\n".join(h["text"] for h in selected),
    )
    if not enumeration and not references:
        return []
    sources = await authorized_sources(session, tenant_id, project_ids=project_ids)
    wanted = {h["source_id"] for h in selected}
    ready = {
        s.id: s
        for s in sources
        if s.id in wanted
        and s.kind == "knowledge"
        and s.phase == "ready"
        and s.index_identity == index_identity()
        and s.indexed_generation == s.generation
    }
    if not ready:
        return []
    rows = list(
        await session.scalars(
            select(RetrievalChunk)
            .where(RetrievalChunk.tenant_id == tenant_id, RetrievalChunk.source_id.in_(ready))
            .order_by(RetrievalChunk.source_id, RetrievalChunk.ordinal)
        )
    )
    terms = set(lexemes(query).split())
    candidates = []
    for c in rows:
        source = ready[c.source_id]
        if c.generation != source.generation or c.citation_id in existing:
            continue
        heading_terms = set(lexemes(c.heading).split())
        relevant = len(terms & heading_terms) + 2 * sum(
            r in c.heading or r in c.body for r in references
        )
        if enumeration and re.search(r"字段|属性|时效|数据|编码", c.heading):
            relevant += 1
        if relevant:
            candidates.append((relevant, c, source))
    candidates.sort(key=lambda item: (-item[0], item[1].source_id, item[1].ordinal))
    hits, counts = [], {}
    for _, c, s in candidates:
        if counts.get(s.id, 0) >= 8:
            continue
        counts[s.id] = counts.get(s.id, 0) + 1
        hits.append(
            {
                "id": c.citation_id,
                "chunk_id": c.id,
                "source_id": s.id,
                "document_id": s.origin_id,
                "title": s.title,
                "heading": c.heading,
                "text": c.body,
                "ordinal": c.ordinal,
                "generation": c.generation,
                "version": s.version,
                "score": None,
                "source": "企业知识库",
                "supplemented": True,
            }
        )
        if len(hits) == 16:
            break
    return await rank(query, hits, with_scores=True)


async def inspect(session, tenant_id, query, project_ids, budget=1200, *, threshold=None):
    if get_settings().rag_mode != "real":
        raise HTTPException(503, "精选证据查验需要真实检索服务")
    threshold = calibrated_threshold() if threshold is None else threshold
    original = await retrieve(
        session,
        tenant_id,
        query,
        project_ids=project_ids,
        knowledge_only=True,
        inspection_candidates=True,
    )
    for hit in original:
        hit["supplemented"] = False
    primary = [h for h in original if h["rerank_score"] >= threshold]
    extra = await supplement(
        session, tenant_id, query, project_ids, primary, {h["id"] for h in original}
    )
    candidates = original + extra
    relevant = sorted(
        [h for h in candidates if h["rerank_score"] >= threshold], key=lambda h: -h["rerank_score"]
    )
    unique, duplicates = deduplicate(relevant)
    texts = [serialize_evidence(original), serialize_evidence(relevant), serialize_evidence(unique)]
    raw_tokens, relevant_tokens, unique_tokens = await token_counts(texts)
    final, omitted, total = [], [], 0
    for hit in unique:
        if len(final) == 8:
            omitted.append(hit["id"])
            continue
        count = (await token_counts([serialize_evidence([*final, hit])]))[0]
        if count <= budget:
            final.append(hit)
            total = count
            continue
        parts = units(hit["text"])
        kept = []
        for part in parts:
            candidate = {**hit, "text": "\n\n".join([*kept, part])}
            count = (await token_counts([serialize_evidence([*final, candidate])]))[0]
            if count <= budget:
                kept.append(part)
        if kept:
            final.append({**hit, "text": "\n\n".join(kept), "partial": True})
            total = (await token_counts([serialize_evidence(final)]))[0]
        omitted.append(hit["id"])
    final_ids = {h["id"] for h in final}
    for hit in final:
        hit["selection_reason"] = "相关章节补充" if hit["supplemented"] else "重排相关性达标"
        hit["source_chunk_ids"] = [hit["chunk_id"]]
    diagnostics = []
    for h in candidates:
        reason = (
            "因预算省略部分或全部内容"
            if h["id"] in omitted
            else "精选证据"
            if h["id"] in final_ids
            else "重复内容"
            if h["id"] in duplicates
            else "相关性不足"
        )
        diagnostics.append({**h, "disposition": reason})
    return {
        "chunks": final,
        "status": "budget_limited"
        if omitted
        else "found"
        if final
        else "insufficient_relevance"
        if original
        else "not_found",
        "diagnostics": {
            "candidate_count": len(original),
            "supplement_count": len(extra),
            "relevant_count": len(relevant),
            "selected_count": len(final),
            "original_tokens": raw_tokens,
            "final_tokens": total,
            "dedup_saved_tokens": max(0, relevant_tokens - unique_tokens),
            "budget": budget,
            "omitted_ids": omitted,
            "tokenizer": "BGE 检索 tokenizer（非生成模型 token）",
            "threshold": threshold,
            "candidates": diagnostics,
        },
        "evidence_text": serialize_evidence(final),
        "pipeline": "inspection_v2",
    }
