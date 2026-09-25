"""V3 retrieval and evidence assembly for inspection, chat, and Agent."""

import asyncio
import json
import re

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import aliased, load_only

from backend.config import get_settings
from backend.knowledge import lexemes
from backend.models import KnowledgeDocument
from backend.rag_v3_index import exact_terms, identity
from backend.rag_v3_models import V3Document, V3Node, V3Unit
from backend.rag_v3_policy import (
    PIPELINE_SCHEMA,
    query_policy,
    retrieval_question,
    structural_relation,
)
from backend.rag_v3_release import classify, features, release
from backend.rag_v3_runtime import embeddings, rank, token_counts


def needs_retrieval(question):
    # A small positive allowlist, never a domain or question-prefix denylist.
    return not bool(
        re.fullmatch(
            r"(?:你好|您好|嗨|谢谢|再见|hello|hi|thanks|thank you|bye)[!！。.\s]*",
            question.strip(),
            re.I,
        )
    )


def visible_documents(tenant, projects):
    newer = aliased(KnowledgeDocument)
    enabled = []
    if not get_settings().rag_pdf_enabled:
        enabled.append(~KnowledgeDocument.source.ilike("%.pdf"))
    if not get_settings().rag_office_enabled:
        enabled.extend((~KnowledgeDocument.source.ilike("%.pptx"),
                        ~KnowledgeDocument.source.ilike("%.xlsx")))
    return (
        select(V3Document, KnowledgeDocument)
        .options(
            load_only(V3Document.id, V3Document.phase, V3Document.identity, raiseload=True),
            load_only(
                KnowledgeDocument.id,
                KnowledgeDocument.title,
                KnowledgeDocument.version,
                KnowledgeDocument.source,
                raiseload=True,
            ),
        )
        .join(KnowledgeDocument, V3Document.origin_id == KnowledgeDocument.id)
        .where(
            V3Document.tenant_id == tenant,
            KnowledgeDocument.tenant_id == tenant,
            KnowledgeDocument.active.is_(True),
            *enabled,
            or_(KnowledgeDocument.project_id.is_(None), KnowledgeDocument.project_id.in_(projects)),
            ~exists(
                select(newer.id).where(
                    newer.tenant_id == tenant,
                    newer.title == KnowledgeDocument.title,
                    newer.version > KnowledgeDocument.version,
                )
            ),
        )
    )


def serialize(hit):
    position = json.dumps(
        hit.get("location", {}), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    refs = ", ".join(hit.get("source_chunk_ids", [hit["id"]]))
    return f"[{refs}] {hit['title']} · v{hit['version']}\n{hit.get('heading', '')} · {hit.get('kind', '')} · {position}\n{hit['text']}"


async def counts(texts):
    result = []
    for start in range(0, len(texts), 64):
        result.extend(await token_counts(texts[start : start + 64]))
    return result


async def rerank(question, hits):
    output = []
    for start in range(0, len(hits), 8):
        batch = hits[start : start + 8]
        originals = {h["id"]: h for h in batch}
        scored = await rank(
            question,
            [
                {
                    **h,
                    "text": (h["context_anchor"] + "\n" if h.get("context_anchor") else "")
                    + h["text"],
                }
                for h in batch
            ],
            with_scores=True,
        )
        output.extend(
            {
                **originals[h["id"]],
                **{k: h[k] for k in ("rerank_score", "rerank_model", "rerank_revision")},
            }
            for h in scored
        )
    return sorted(output, key=lambda h: (-h["rerank_score"], h["id"]))


async def recall(session, tenant, question, documents, policy=None):
    from backend.rag_v3_search import search

    ids = list(documents)
    if not ids:
        return [], {"bm25": 0, "vector": 0, "exact": 0}
    policy = policy or query_policy(question)
    limit = policy.recall_limit
    base = select(V3Unit).where(V3Unit.tenant_id == tenant, V3Unit.document_id.in_(ids))
    # Network branches run concurrently; SQL uses a single session sequentially.
    lexical, vectors = await asyncio.gather(
        search(tenant, ids, lexemes(question), limit=limit), embeddings([question], query=True)
    )
    distance = V3Unit.embedding.op("<=>")(vectors[0])
    vector = (
        await session.execute(
            select(V3Unit.id, distance.label("distance"))
            .where(
                V3Unit.tenant_id == tenant,
                V3Unit.document_id.in_(ids),
                V3Unit.embedding.is_not(None),
            )
            .order_by(distance, V3Unit.id)
            .limit(limit)
        )
    ).all()
    terms = exact_terms(question) + re.findall(r'[“"]([^”"\n]+)[”"]', question)
    if "identifier" in policy.types:
        identifiers = [
            term for term in terms if term.startswith("$[") or re.search(r"[-_./:]|\d", term)
        ]
        identifiers.extend(re.findall(r"编号\s*[:：]?\s*([A-Za-z0-9_.-]+)", question))
        if identifiers:
            terms = identifiers
    exact = (
        list(
            await session.scalars(
                base.where(or_(*[V3Unit.text.contains(t, autoescape=True) for t in terms]))
                .order_by(V3Unit.id)
                .limit(limit)
            )
        )
        if terms
        else []
    )
    routes = {
        "bm25": lexical,
        "vector": [(i, 1 - d) for i, d in vector],
        "exact": [(u.id, 1.0) for u in exact],
    }
    fusion = {}
    scores = {}
    for route, entries in routes.items():
        for n, (key, value) in enumerate(entries, 1):
            fusion[key] = fusion.get(key, 0) + (
                2 if route == "exact" and "identifier" in policy.types else 1
            ) / (60 + n)
            scores.setdefault(key, {})[route] = value
    ordered = sorted(fusion, key=lambda k: (-fusion[k], k))[: limit * 2]
    # Recheck OpenSearch IDs against SQL authority before returning any content.
    units = {u.id: u for u in await session.scalars(base.where(V3Unit.id.in_(ordered)))}
    hits = []
    for key in ordered:
        if key not in units:
            continue
        u = units[key]
        source, doc = documents[u.document_id]
        hits.append(hit(u, doc, score=fusion[key], recall_scores=scores[key]))
    return hits, {key: len(value) for key, value in routes.items()}


def hit(unit, doc, **extra):
    return {
        "id": unit.id,
        "source_chunk_ids": [unit.id],
        "document_id": unit.document_id,
        "origin_id": doc.id,
        "title": doc.title,
        "version": doc.version,
        "heading": unit.heading,
        "kind": unit.kind,
        "location": unit.location,
        "text": unit.text,
        "record": unit.record,
        "parent": unit.parent,
        "node_id": unit.node_id,
        "ordinal": unit.ordinal,
        "previous_id": unit.previous_id,
        "next_id": unit.next_id,
        "source": doc.source,
        "supplemented": False,
        **extra,
    }


async def supplement(session, tenant, selected, documents, policy=None):
    policy = policy or query_policy("")
    output = []
    seen = {h["id"] for h in selected}
    for item in selected:
        if item["document_id"] not in documents:
            continue
        relations = [V3Unit.node_id == item["node_id"]]
        if item.get("record"):
            relations.append(V3Unit.record == item["record"])
        # Only explicit references; parent titles are already included in each unit.
        references = re.findall(
            r'(?:参见|见章节|see section)\s*[“「"]([^”」"\n]+)[”」"]', item["text"], re.I
        )
        relations.extend(V3Unit.heading == r for r in references)
        if item.get("heading"):
            relations.extend(
                V3Unit.text.contains(prefix + item["heading"] + end, autoescape=True)
                for prefix, end in [
                    ("参见“", "”"),
                    ("见章节“", "”"),
                    ('see section "', '"'),
                    ("参见「", "」"),
                ]
            )
        if policy.enumeration:
            location = item.get("location", {})
            if location.get("list_scope"):
                relations.append(
                    V3Unit.location["list_scope"].as_string() == location["list_scope"]
                )
            record = item.get("record") or ""
            if item["kind"] in {"table_cell", "table_record"}:
                prefix = (
                    record.rsplit(":row:", 1)[0] + ":row:"
                    if ":row:" in record
                    else "row:"
                    if record.startswith("row:")
                    else None
                )
                if prefix:
                    relations.append(V3Unit.record.startswith(prefix, autoescape=True))
            if item["kind"] == "json_value" and item.get("parent") is not None:
                relations.append((V3Unit.parent == item["parent"]) & (V3Unit.kind == "json_value"))
        rows = await session.scalars(
            select(V3Unit)
            .where(
                V3Unit.tenant_id == tenant,
                V3Unit.document_id == item["document_id"],
                or_(*relations),
                V3Unit.id.not_in(seen),
            )
            .order_by(V3Unit.ordinal)
            .limit(policy.repair_limit - len(output) + 1)
        )
        rows = list(rows)
        room = policy.repair_limit - len(output)
        if len(rows) > room:
            item["repair_limited"] = True
        for unit in rows[:room]:
            seen.add(unit.id)
            output.append(
                hit(
                    unit,
                    documents[unit.document_id][1],
                    supplemented=True,
                    supplement_from=item["id"],
                    context_anchor=item["text"],
                    score=None,
                    recall_scores={},
                    repair_relation=structural_relation(
                        item,
                        hit(unit, documents[unit.document_id][1]),
                        enumeration=policy.enumeration,
                    ),
                )
            )
    return output


async def assemble(relevant, budget, policy=None):
    from backend.rag_v3_evidence import assemble_evidence

    return await assemble_evidence(relevant, budget, counts, serialize, policy)


async def inspect(session, tenant, question, projects, budget):
    from backend.config import get_settings

    settings = get_settings()
    config = (
        release()
        if settings.rag_v3_relevance_mode == "calibrated"
        else {"relevance_mode": "rules", "min_rerank_score": settings.rag_v3_min_rerank_score}
    )
    result = await run(session, tenant, question, projects, budget, config)
    result["relevance_mode"] = settings.rag_v3_relevance_mode
    result["quality_calibrated"] = settings.rag_v3_relevance_mode == "calibrated"
    return result


def classify_relevance(features, config):
    if config.get("relevance_mode") == "rules":
        # The deployed CrossEncoder returns sigmoid scores. This explicit
        # threshold is a heuristic, not calibrated answer confidence.
        score = features["rerank"]
        return score >= config["min_rerank_score"], score
    return classify(features, config["classifier"])


async def run(session, tenant, question, projects, budget, config):
    """Internal offline evaluator entry. The HTTP route always uses inspect/release."""
    rows = (await session.execute(visible_documents(tenant, projects))).all()
    documents = {s.id: (s, d) for s, d in rows if s.phase == "ready" and s.identity == identity()}
    pending = sum(s.phase != "ready" or s.identity != identity() for s, d in rows)
    if not documents:
        return {
            "pipeline": "v3",
            "pipeline_schema": PIPELINE_SCHEMA,
            "status": "index_unavailable",
            "chunks": [],
            "evidence_text": "",
            "query_types": list(query_policy(question).types),
            "result_status": "insufficient",
            "budget_limited": False,
            "conflict": False,
            "completeness": "unknown",
            "coverage": "unknown",
            "reason": "无可用资料或最新版本索引尚未就绪",
            "pending_documents": pending,
        }
    policy = query_policy(question)
    candidates_by_id, routes, branches = {}, {}, []
    for branch, query in enumerate(policy.queries):
        hits, route_counts = await recall(session, tenant, query, documents, policy)
        branches.append(hits)
        for name, value in route_counts.items():
            routes[name] = routes.get(name, 0) + value
        for h in hits:
            candidate = candidates_by_id.setdefault(h["id"], h)
            candidate.setdefault("query_branches", []).append(branch)
    # Interleave comparison branches before the bounded reranker input.
    candidates, visited = [], set()
    for index in range(max(map(len, branches), default=0)):
        for branch in branches:
            if index < len(branch) and branch[index]["id"] not in visited:
                visited.add(branch[index]["id"])
                candidates.append(candidates_by_id[branch[index]["id"]])
    ranking_question = retrieval_question(question)
    ranked = await rerank(ranking_question, candidates[: policy.rerank_limit])
    best = ranked[0]["rerank_score"] if ranked else 0
    direct, relevant = [], []
    for h in ranked:
        h["features"] = features(question, h, best)
        accepted, h["relevance_value"] = classify_relevance(h["features"], config)
        h["evidence_role"] = "direct" if accepted else "irrelevant"
        h["disposition"] = h["evidence_role"]
        if accepted:
            direct.append(h)
            relevant.append(h)
    # Supporting requires a concrete direct anchor, never just a lower score.
    for h in ranked:
        anchors = [core["id"] for core in direct if structural_relation(core, h)]
        if anchors:
            h["supports"] = anchors
            if h["evidence_role"] == "irrelevant":
                h["evidence_role"] = h["disposition"] = "supporting"
                relevant.append(h)
    additions = await supplement(session, tenant, direct, documents, policy)
    additions = await rerank(ranking_question, additions)
    known = {h["id"]: h for h in relevant}
    for h in additions:
        h["features"] = features(question, h, best)
        _, h["relevance_value"] = classify_relevance(h["features"], config)
        relation = h.get("repair_relation")
        h["evidence_role"] = (
            "direct" if relation == "enumeration" else "supporting" if relation else "irrelevant"
        )
        h["disposition"] = h["evidence_role"]
        h["supports"] = [] if relation == "enumeration" else [h["supplement_from"]]
        if h["id"] in known:
            known[h["id"]]["supports"] = list(
                set(known[h["id"]].get("supports", [])) | set(h["supports"])
            )
        elif h["evidence_role"] != "irrelevant":
            relevant.append(h)
            known[h["id"]] = h
    # Shared repairs must stay attached to every applicable core, even when
    # fetched once. Enumeration peers are distinct facts, not one giant package.
    for h in relevant:
        anchors = [
            core["id"]
            for core in relevant
            if core.get("evidence_role") == "direct"
            and structural_relation(core, h, enumeration=policy.enumeration)
            in {"record", "node_parts", "explicit_reference"}
        ]
        h["supports"] = list(set(h.get("supports", [])) | set(anchors))
    node_ids = {h["node_id"] for h in relevant if h.get("location", {}).get("requires_all_parts")}
    if node_ids:
        nodes = await session.scalars(
            select(V3Node).where(
                V3Node.tenant_id == tenant,
                V3Node.document_id.in_(documents),
                V3Node.id.in_(node_ids),
            )
        )
        originals = {
            n.id: (n.structure.get("context", "") + "\n" if n.structure.get("context") else "")
            + n.structure["text"]
            for n in nodes
        }
        for h in relevant:
            if h["node_id"] in originals:
                h["complete_node_text"] = originals[h["node_id"]]
    # Recheck the SQL authority before assembly, including diagnostic content.
    current = (
        await session.execute(
            visible_documents(tenant, projects).execution_options(populate_existing=True)
        )
    ).all()
    allowed = {s.id for s, d in current if s.phase == "ready" and s.identity == identity()}
    relevant = [h for h in relevant if h["document_id"] in allowed]
    ranked = [h for h in ranked if h["document_id"] in allowed]
    additions = [h for h in additions if h["document_id"] in allowed]
    candidates = [h for h in candidates if h["document_id"] in allowed]
    selected, evidence, total, saved, omitted, conflicts = await assemble(relevant, budget, policy)
    original = sum(await counts([serialize(h) for h in candidates]))
    repair_limited = any(h.get("repair_limited") for h in relevant)
    budget_limited = repair_limited or any(
        h["id"] in omitted and h.get("disposition") in {"token_budget", "unit_limit"}
        for h in relevant
    )
    # Keep legacy status values for existing consumers; flags are independent.
    status = (
        "source_conflict"
        if conflicts
        else "budget_limited"
        if budget_limited
        else "evidence_found"
        if selected
        else "insufficient_relevance"
    )
    omission_details = [
        {"source_id": h["id"], "reason": h.get("disposition", "incomplete_structure")}
        for h in relevant
        if h["id"] in omitted
    ]
    omission_details.extend(
        {"source_id": h["id"], "reason": "repair_limit"}
        for h in relevant
        if h.get("repair_limited")
    )
    return {
        "pipeline": "v3",
        "pipeline_schema": PIPELINE_SCHEMA,
        "status": status,
        "query_types": list(policy.types),
        "result_status": "evidence_found" if selected else "insufficient",
        "budget_limited": budget_limited,
        "conflict": bool(conflicts),
        "chunks": selected,
        "evidence_text": evidence,
        "coverage": "unknown",
        "completeness": "unknown",
        "pending_documents": pending,
        "conflicts": conflicts,
        "omitted": omitted,
        "omission_details": omission_details,
        "diagnostics": {
            "candidate_count": len(candidates),
            "routes": routes,
            "rerank_count": len(ranked),
            "supplement_count": len(additions),
            "relevant_count": len(relevant),
            "selected_count": len(selected),
            "selected_unit_count": sum(len(h["source_chunk_ids"]) for h in selected),
            "original_tokens": original,
            "final_tokens": total,
            "dedup_saved_tokens": saved,
            "budget": budget,
            "unit_limit": policy.unit_limit,
            "repair_limit": policy.repair_limit,
            "tokenizer": "BGE 检索 tokenizer token",
            "candidates": ranked
            + additions
            + [{**h, "disposition": "未进入重排上限"} for h in candidates[policy.rerank_limit :]],
        },
    }
