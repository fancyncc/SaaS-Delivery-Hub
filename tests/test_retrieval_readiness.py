"""Readiness must agree across document UI, inspection and the indexing scheduler."""
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select

from backend.config import get_settings
from backend.db import SessionLocal
from backend.knowledge_index import identity, process, register
from backend.knowledge_retrieval import run
from backend.models import KnowledgeDocument, Tenant


def test_default_compose_starts_indexing_without_agent_profile():
    services = yaml.safe_load(Path('docker-compose.yml').read_text(encoding='utf-8'))['services']
    for name in ('indexer', 'beat'):
        assert not services[name].get('profiles')
        assert services[name]['restart'] == 'unless-stopped'


@pytest.mark.parametrize('phase,stored_identity,reason', [
    (None, '', 'index_pending'),
    ('ready', 'old-parser-identity', 'stale_index'),
    ('failed', '', 'index_failed'),
])
async def test_unavailable_reports_actual_reason_and_never_claims_retrieval(
    monkeypatch, phase, stored_identity, reason,
):
    monkeypatch.setattr(get_settings(), 'retrieval_indexing_enabled', True)

    async def forbidden(*args, **kwargs):
        raise AssertionError('No model or search calls before the readiness barrier')

    monkeypatch.setattr('backend.knowledge_retrieval.recall', forbidden)
    async with SessionLocal() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == 'legacy-demo'))
        doc = KnowledgeDocument(tenant_id=tenant.id, title='成员迁移说明', version=1,
                                module='test', source='migration.md', license='internal',
                                body='成员迁移前校验字段，通过审批后执行导入。')
        session.add(doc)
        await session.flush()
        if phase:
            source = await register(session, doc)
            source.phase, source.identity = phase, stored_identity
            await session.flush()
        result = await run(session, tenant.id, '成员迁移流程是怎样的', [], 1200, {})
        assert result['status'] == 'index_unavailable'
        assert result['unavailable_reason'] == reason
        assert result['retrieval_executed'] is False
        assert result['index_availability']['visible_documents'] == 1
        assert result['index_availability']['ready_documents'] == 0
        assert result['diagnostics']['candidate_count'] == 0
        assert result['diagnostics']['timings_ms']['total'] >= 0
        # An unrelated tenant cannot learn that this document exists.
        hidden = await run(session, 'unrelated-tenant', '成员迁移流程', [], 1200, {})
        assert hidden['unavailable_reason'] == 'no_documents'
        assert hidden['pending_documents'] == 0


async def test_stale_document_ui_and_rebuild_agree(client, monkeypatch):
    async with SessionLocal() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == 'legacy-demo'))
        doc = KnowledgeDocument(tenant_id=tenant.id, title='回滚预案', version=1,
                                module='test', source='rollback.md', license='internal',
                                body='跨站数据泄露立即回滚，错误率超标也触发回滚。')
        session.add(doc)
        await session.flush()
        source = await register(session, doc)
        source.phase, source.identity = 'ready', 'old-parser-identity'
        doc_id, tenant_id = doc.id, tenant.id
        await session.commit()
    detail = (await client.get(f'/api/knowledge/{doc_id}')).json()['data']
    listed = (await client.get('/api/knowledge')).json()['data']
    assert detail['index']['phase'] == 'stale'
    assert detail['can_reindex'] is True
    assert next(d for d in listed if d['id'] == doc_id)['index_status'] == 'indexed_stale'

    async def rebuild(session, source):
        source.phase, source.identity = 'ready', identity()

    monkeypatch.setattr('backend.knowledge_index.index_document', rebuild)
    async with SessionLocal() as session:
        await process(session, tenant_id)
        await session.commit()
    detail = (await client.get(f'/api/knowledge/{doc_id}')).json()['data']
    assert detail['index']['phase'] == 'ready'
    assert detail['index_status'] == 'indexed_ready'


async def test_query_expansion_cannot_replace_original_reranking_intent(monkeypatch):
    from backend import knowledge_retrieval as pipeline
    from backend.knowledge_index import index_document
    from backend.knowledge_index_models import RetrievalUnit

    async def counts(texts):
        return [len(text) // 4 for text in texts]

    monkeypatch.setattr('backend.knowledge_index.model_counts', counts)
    monkeypatch.setattr(pipeline, 'token_counts', counts)
    question = '成员迁移流程是怎样的'
    queries, ranking_queries = [], []
    async with SessionLocal() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == 'legacy-demo'))
        doc = KnowledgeDocument(tenant_id=tenant.id, title='成员迁移操作手册', version=1,
                                module='test', source='migration.md', license='internal',
                                body='# 操作顺序\n\n配置审批完成后上传成员 CSV，校验通过后审批并导入，最后核对人数。\n\n# 无关说明\n\n界面可以切换主题。')
        session.add(doc)
        await session.flush()
        source = await register(session, doc)
        await index_document(session, source)
        units = list(await session.scalars(select(RetrievalUnit).where(RetrievalUnit.document_id == source.id)))

        async def recall(session, tenant, query, documents, policy):
            queries.append(query)
            return [pipeline.hit(unit, doc) for unit in units], {'vector': len(units)}

        async def rank(query, hits, **kwargs):
            ranking_queries.append(query)
            # Models can score appended glossary queries very differently.
            return [{**h, 'rerank_score': .8 if query == question and 'CSV' in h['text'] else .01,
                     'rerank_model': 'test', 'rerank_revision': 'test'} for h in hits]

        monkeypatch.setattr(pipeline, 'recall', recall)
        monkeypatch.setattr(pipeline, 'rank', rank)
        result = await run(session, tenant.id, question, [], 1200,
                           {'relevance_mode': 'rules', 'min_rerank_score': .5})
        assert len(queries) > 1 and any('migration' in query for query in queries)
        assert ranking_queries and all(query == question for query in ranking_queries)
        assert result['retrieval_executed'] is True
        assert result['status'] == 'evidence_found'
        assert len(result['chunks']) == 1 and 'CSV' in result['chunks'][0]['text']
        assert '主题' not in result['evidence_text']
