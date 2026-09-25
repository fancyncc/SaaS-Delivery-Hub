import json
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy import select

from backend import rag_v3 as pipeline
from backend.db import SessionLocal
from backend.models import KnowledgeDocument, Tenant
from backend.rag_v3_index import index_document, register
from backend.rag_v3_policy import query_policy
from backend.rag_v3_release import FEATURES
from tests.test_rag_v3 import evidence


async def tokens(texts):
    return [max(1, len(t)//4) if t else 0 for t in texts]


@pytest.mark.parametrize('question, expected', [
    ('列出全部退款条件', {'enumeration', 'condition'}),
    ('比较“Basic”和“Pro”的所有字段', {'comparison', 'enumeration'}),
    ('What is E_403?', {'identifier'}), ('说明产品能力', {'fact'}),
])
def test_query_types_do_not_gate_retrieval(question, expected):
    assert set(query_policy(question).types) == expected
    assert pipeline.needs_retrieval(question)


def test_attribute_questions_expand_field_intent_without_changing_original():
    from backend.rag_v3_policy import retrieval_question
    question = '售后工单的属性有哪些'
    policy = query_policy(question)
    assert policy.queries[0] == question
    assert retrieval_question(question) in policy.queries
    assert '字段' in retrieval_question(question)
    assert retrieval_question('首次响应时限是多少') == '首次响应时限是多少'


async def test_enumeration_exceeds_eight_without_raising_token_budget(monkeypatch):
    monkeypatch.setattr(pipeline, 'token_counts', tokens)
    rows = [evidence(i, f'条件 {i} 必须满足') for i in range(12)]
    policy = query_policy('列出全部条件')
    selected, _, used, _, omitted, _ = await pipeline.assemble(rows, 1200, policy)
    assert len(selected) == 12 and not omitted and used <= 1200
    normal, _, _, _, skipped, _ = await pipeline.assemble(rows, 1200)
    assert len(normal) == 8 and len(skipped) == 4


async def test_rule_and_exception_are_atomic(monkeypatch):
    monkeypatch.setattr(pipeline, 'token_counts', tokens)
    core = evidence(0, '退款允许', evidence_role='direct', rerank_score=.9)
    exception = evidence(1, '已使用产品不允许退款。'*80, evidence_role='supporting', supports=['0'])
    other = evidence(2, '申请须在七天内提交', evidence_role='direct', rerank_score=.8)
    chosen, text, _, _, omitted, _ = await pipeline.assemble([core, exception, other], 100)
    assert [h['id'] for h in chosen] == ['2']
    assert set(omitted) == {'0', '1'} and '退款允许' not in text


async def test_support_alone_cannot_answer(monkeypatch):
    monkeypatch.setattr(pipeline, 'token_counts', tokens)
    chosen, *_ = await pipeline.assemble([evidence(1, '表头', evidence_role='supporting', supports=['missing'])], 600)
    assert chosen == []


async def test_nested_known_dependencies_cannot_be_cut_from_core(monkeypatch):
    monkeypatch.setattr(pipeline, 'token_counts', tokens)
    rows = [evidence(0, '主规则', evidence_role='direct', rerank_score=1),
            evidence(1, '定义', evidence_role='direct', supports=['0'], rerank_score=.8),
            evidence(2, '必要限制'*500, evidence_role='supporting', supports=['1'])]
    chosen, _, _, _, omitted, _ = await pipeline.assemble(rows, 100)
    assert not chosen and set(omitted) == {'0', '1', '2'}


async def test_missing_exception_parts_block_attached_rule(monkeypatch):
    monkeypatch.setattr(pipeline, 'token_counts', tokens)
    core = evidence(0, '主规则', evidence_role='direct')
    exception = evidence(1, '例外前半段', evidence_role='supporting', supports=['0'], node_id='n')
    exception['location'] = {'requires_all_parts': True, 'parts': 2}
    chosen, _, _, _, omitted, _ = await pipeline.assemble([core, exception], 600)
    assert not chosen and set(omitted) == {'0', '1'}


async def test_duplicate_fact_does_not_crowd_out_distinct_conditions(monkeypatch):
    monkeypatch.setattr(pipeline, 'token_counts', tokens)
    rows = [evidence(i, '必须未使用', rerank_score=1-i*.01) for i in range(8)]
    rows += [evidence(9, '必须在七天内'), evidence(10, '必须提供凭证')]
    chosen, _, _, saved, _, _ = await pipeline.assemble(rows, 600)
    assert len(chosen) == 3 and saved > 0
    assert chosen[0]['duplicate_source_ids'] == [str(i) for i in range(1, 8)]


async def test_literal_fact_coverage_precedes_relevance(monkeypatch):
    monkeypatch.setattr(pipeline, 'token_counts', tokens)
    rows = [evidence(0, '必须未使用。必须七天内提交。', rerank_score=1),
            evidence(1, '必须未使用。必须提供凭证。', rerank_score=.9),
            evidence(2, '订阅服务不适用。', rerank_score=.5)]
    chosen, *_ = await pipeline.assemble(rows, 600)
    assert [h['id'] for h in chosen] == ['0', '2', '1']


async def test_sql_recall_prefilters_and_rejects_foreign_index_ids(monkeypatch):
    from backend.rag_v3_models import V3Unit
    monkeypatch.setattr('backend.rag_v3_index.model_counts', tokens)
    async with SessionLocal() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == 'legacy-demo'))
        docs, sources, units = [], [], []
        for title in ('Allowed', 'Secret'):
            doc = KnowledgeDocument(tenant_id=tenant.id, title=title, version=1, module='test',
                                    source='original', license='test', body=f'{title} E_403')
            session.add(doc)
            await session.flush()
            source = await register(session, doc)
            await index_document(session, source)
            docs.append(doc)
            sources.append(source)
            units.append(await session.scalar(select(V3Unit).where(V3Unit.document_id == source.id)))
        async def search(tenant_id, ids, query, **kw):
            assert tenant_id == tenant.id and ids == [sources[0].id]
            return [(units[1].id, 99), (units[0].id, 1)]
        async def vectors(*a, **kw):
            return [[0.0]*512]
        class QuerySession:
            async def execute(self, statement):
                # SQLite cannot execute pgvector; inspect the real vector SQL
                # constraints and substitute only the distance calculation.
                params = statement.compile().params
                assert tenant.id in params.values()
                assert [sources[0].id] in params.values()
                assert statement._where_criteria
                class Result:
                    def all(self):
                        return [(units[0].id, 0.0)]
                return Result()
            async def scalars(self, statement):
                params = statement.compile().params
                assert tenant.id in params.values() and [sources[0].id] in params.values()
                return await session.scalars(statement)
        monkeypatch.setattr('backend.rag_v3_search.search', search)
        monkeypatch.setattr(pipeline, 'embeddings', vectors)
        hits, _ = await pipeline.recall(QuerySession(), tenant.id, 'E_403', {sources[0].id: (sources[0], docs[0])})
        assert [h['id'] for h in hits] == [units[0].id]
        assert all('Secret' not in h['text'] for h in hits)
        assert await pipeline.supplement(session, tenant.id, [pipeline.hit(units[1], docs[1])],
                                         {sources[0].id: (sources[0], docs[0])}) == []


async def test_structure_expansion_has_a_hard_limit(monkeypatch):
    from backend.rag_v3_models import V3Unit
    monkeypatch.setattr('backend.rag_v3_index.model_counts', tokens)
    async with SessionLocal() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == 'legacy-demo'))
        doc = KnowledgeDocument(tenant_id=tenant.id, title='Long list', version=1, module='test',
            source='original', license='test', body='# 条件\n'+'\n'.join(f'- 条件 {i}' for i in range(80)))
        session.add(doc)
        await session.flush()
        source = await register(session, doc)
        await index_document(session, source)
        unit = await session.scalar(select(V3Unit).where(V3Unit.document_id == source.id).order_by(V3Unit.ordinal))
        core = pipeline.hit(unit, doc)
        additions = await pipeline.supplement(session, tenant.id, [core], {source.id: (source, doc)}, query_policy('所有条件'))
        assert len(additions) == 64 and core['repair_limited']


async def test_conflict_survives_budget_omission(monkeypatch):
    monkeypatch.setattr(pipeline, 'token_counts', tokens)
    a = evidence(0, '12', rerank_score=1)
    b = evidence(1, '13'+' extra'*200, rerank_score=.5)
    a['location'] = b['location'] = {'path': '$["limit"]'}
    chosen, _, _, _, omitted, conflicts = await pipeline.assemble([a, b], 80)
    assert len(chosen) == 1 and omitted == ['1'] and conflicts == [['0', '1']]


async def test_comparison_preserves_both_sides(monkeypatch):
    monkeypatch.setattr(pipeline, 'token_counts', tokens)
    rows = [evidence(i, 'side A '+str(i), query_branches=[1], rerank_score=1-i*.01) for i in range(8)]
    rows += [evidence(9, 'side B', query_branches=[2], rerank_score=.2)]
    chosen, *_ = await pipeline.assemble(rows, 65, query_policy('比较 A 和 B 的字段'))
    assert [h['id'] for h in chosen[:2]] == ['0', '9']


async def test_empty_acl_never_contacts_search_or_embedding(monkeypatch):
    async def forbidden(*a, **kw):
        raise AssertionError('empty authorization must short circuit')
    monkeypatch.setattr('backend.rag_v3_search.search', forbidden)
    monkeypatch.setattr(pipeline, 'embeddings', forbidden)
    assert (await pipeline.recall(None, 'tenant', 'question', {}))[0] == []


async def test_bm25_acl_is_inside_query(monkeypatch):
    from backend import rag_v3_search
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'hits': {'hits': []}})
    @asynccontextmanager
    async def client():
        async with httpx.AsyncClient(base_url='http://index.test', transport=httpx.MockTransport(handler)) as http:
            yield http
    monkeypatch.setattr(rag_v3_search, 'client', client)
    await rag_v3_search.search('tenant-a', ['authorized'], 'secret', limit=60)
    assert requests[0]['query']['bool']['filter'] == [
        {'term': {'tenant_id': 'tenant-a'}}, {'terms': {'document_id': ['authorized']}}]
    await rag_v3_search.search('tenant-a', [], 'secret')
    assert len(requests) == 1


async def test_enumeration_repairs_only_target_list_and_rechecks_acl(monkeypatch):
    from backend.rag_v3_models import V3Unit
    monkeypatch.setattr('backend.rag_v3_index.model_counts', tokens)
    monkeypatch.setattr(pipeline, 'token_counts', tokens)
    async with SessionLocal() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == 'legacy-demo'))
        doc = KnowledgeDocument(tenant_id=tenant.id, title='退款', version=1, module='test',
            source='original', license='test', body='# 退款条件\n'+ '\n'.join(f'- 条件{i}' for i in range(12))+'\n\n无关段落\n\n- 无关列表')
        session.add(doc)
        await session.flush()
        source = await register(session, doc)
        await index_document(session, source)
        units = list(await session.scalars(select(V3Unit).where(V3Unit.document_id == source.id).order_by(V3Unit.ordinal)))
        assert units[0].location['list_scope'] != units[-1].location['list_scope']
        async def recall(*args, **kwargs):
            return [pipeline.hit(units[0], doc, score=1)], {'bm25': 1}
        async def rank(question, hits):
            return [{**h, 'rerank_score': .9 if h['id'] == units[0].id else .1} for h in hits]
        monkeypatch.setattr(pipeline, 'recall', recall)
        monkeypatch.setattr(pipeline, 'rerank', rank)
        classifier = {'intercept': -5, 'threshold': .5, 'weights': {k: 10 if k == 'rerank' else 0 for k in FEATURES}}
        result = await pipeline.run(session, tenant.id, '列出全部退款条件', [], 1800, {'classifier': classifier})
        assert len(result['chunks']) == 12
        assert '无关' not in result['evidence_text']
        assert result['completeness'] == 'unknown'
        assert not result['budget_limited']
        assert all(h['evidence_role'] == 'direct' for h in result['chunks'])
        # A source revoked after retrieval cannot leak through diagnostics either.
        async def revoke(question, hits):
            doc.active = False
            await session.flush()
            return [{**h, 'rerank_score': .9} for h in hits]
        monkeypatch.setattr(pipeline, 'rerank', revoke)
        result = await pipeline.run(session, tenant.id, '列出全部退款条件', [], 1800, {'classifier': classifier})
        assert not result['chunks'] and not result['diagnostics']['candidates']
