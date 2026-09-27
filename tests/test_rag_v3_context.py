from uuid import uuid4

from sqlalchemy import select

from backend import rag_v3 as pipeline
from backend.db import SessionLocal
from backend.models import KnowledgeDocument, Tenant
from backend.rag_v3_context import identity_contexts, ranking_text
from backend.rag_v3_index import index_document, register
from backend.rag_v3_models import V3Node, V3Unit
from backend.rag_v3_parse import Node, parse
from tests.test_rag_v3_strategy import tokens


def test_explicit_identity_does_not_cross_sections_or_attach_other_objects():
    parsed = parse("x.md", b"# A\n\nid: SKU-204\n\nlimit: 17\n\nother: ARCHIVE-999 limit 999\n\n# B\n\nlimit: 21\n\nText mentions SKU-204.")
    contexts = identity_contexts(parsed.nodes, parsed.nodes)
    by_text = {n.text: n.id for n in parsed.nodes}
    assert contexts == {by_text["limit: 17"]: [by_text["id: SKU-204"]]}
    ambiguous = parse("x.txt", b"id: A-1\n\nlimit: 17\n\nid: B-2\n\nlimit: 21")
    assert not identity_contexts(ambiguous.nodes, ambiguous.nodes)


def test_identity_does_not_cross_pdf_pages():
    nodes = [Node("a", "paragraph", "id: SKU-204", {"page": 1}),
             Node("b", "paragraph", "limit: 17", {"page": 2})]
    assert not identity_contexts(nodes, nodes)


async def test_reranking_keeps_source_body_and_requires_citable_identity(monkeypatch):
    monkeypatch.setattr("backend.rag_v3_index.model_counts", tokens)
    monkeypatch.setattr(pipeline, "token_counts", tokens)
    async with SessionLocal() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == "legacy-demo"))
        doc = KnowledgeDocument(tenant_id=tenant.id, title="Inventory", version=1,
                                module="test", source="inventory.md", license="test",
                                body="# Stock\n\nid: SKU-204\n\nlimit: 17")
        session.add(doc)
        await session.flush()
        source = await register(session, doc)
        await index_document(session, source)
        units = list(await session.scalars(select(V3Unit).where(V3Unit.document_id == source.id)))
        limit = next(u for u in units if u.text == "limit: 17")
        anchor = next(u for u in units if u.text == "id: SKU-204")
        core = pipeline.hit(limit, doc, evidence_role="direct", rerank_score=.9)
        documents = {source.id: (source, doc)}
        await pipeline.attach_identity_context(session, tenant.id, [core], documents)
        assert "SKU-204" in ranking_text(core) and core["text"] == "limit: 17"
        additions = await pipeline.supplement(session, tenant.id, [core], documents)
        assert len(additions) == 1 and additions[0]["id"] == anchor.id
        assert additions[0]["repair_relation"] == "object_identity"
        additions[0].update(evidence_role="supporting", supports=[core["id"]])
        chosen, text, *_ = await pipeline.assemble([core, *additions], 600)
        assert {h["id"] for h in chosen} == {core["id"], anchor.id}
        assert "id: SKU-204" in text and "limit: 17" in text
        assert all("identity_nodes" not in h and "identity_context" not in h for h in chosen)
        chosen, *_ = await pipeline.assemble([core, *additions], 1)
        assert not chosen
        chosen, *_ = await pipeline.assemble([core], 600)
        assert not chosen and core["disposition"] == "missing_object_identity"

        # A forged cross-document metadata reference must not enrich ranking.
        foreign = V3Node(id=str(uuid4()), tenant_id=tenant.id, document_id="not-authorized",
                         structure={"text": "SECRET OTHER PROJECT"})
        session.add(foreign)
        node = await session.get(V3Node, limit.node_id)
        node.structure = {**node.structure, "identity_nodes": [foreign.id]}
        await session.flush()
        new = pipeline.hit(limit, doc)
        await pipeline.attach_identity_context(session, tenant.id, [new], documents)
        assert "SECRET" not in ranking_text(new) and not new["identity_nodes"]
        await session.rollback()


async def test_vertical_key_value_sheet_keeps_saved_cells_and_identity(monkeypatch):
    from pathlib import Path

    monkeypatch.setattr("backend.rag_v3_index.model_counts", tokens)
    async with SessionLocal() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == "legacy-demo"))
        doc = KnowledgeDocument(tenant_id=tenant.id, title="Inventory sheet", version=1,
                                module="test", source="inventory.xlsx", license="test", body="")
        session.add(doc)
        await session.flush()
        source = await register(session, doc, doc.source,
                                Path("evaluations/v3/fixtures/inventory.xlsx").read_bytes())
        await index_document(session, source)
        units = list(await session.scalars(select(V3Unit).where(V3Unit.document_id == source.id)))
        limit = next(u for u in units if "limit" in u.text and "17" in u.text)
        core = pipeline.hit(limit, doc)
        await pipeline.attach_identity_context(session, tenant.id, [core], {source.id: (source, doc)})
        assert "SKU-204" in ranking_text(core)
        assert "SKU-204" not in core["text"] and core["location"]["row"] == 2
        assert "列 1" in core["text"] and "列 2" in core["text"]
        await session.rollback()
