from uuid import uuid4

import pytest


async def test_no_retrieval_for_greeting(client, monkeypatch):
    async def unexpected(*args, **kwargs):
        raise AssertionError("unnecessary retrieval")
    monkeypatch.setattr("backend.rag_v3.inspect", unexpected)
    response = await client.post('/api/knowledge/inspect', json={'question': '你好'})
    assert response.status_code == 200
    assert response.json()['data']['status'] == 'not_needed'
    assert response.json()['data']['chunks'] == []
    assert response.json()['data']['pipeline'] == 'v3'


async def test_empty_library_does_not_return_demo_corpus(client):
    response = await client.post('/api/knowledge/inspect', json={'question': '成员 CSV 导入要求'})
    assert response.status_code == 200
    assert response.json()['data']['status'] == 'index_unavailable'
    assert response.json()['data']['chunks'] == []


async def test_inspection_uses_current_pipeline_and_budget(client, monkeypatch):
    async def inspect(session, tenant_id, question, project_ids, budget):
        assert question == '成员导入规定'
        assert budget == 600
        return {'pipeline': 'v3', 'status': 'evidence_found', 'chunks': [], 'evidence_text': '邮箱必须唯一'}
    monkeypatch.setattr('backend.rag_v3.inspect', inspect)
    response = await client.post('/api/knowledge/inspect', json={'question': '成员导入规定', 'evidence_budget': 600})
    assert response.status_code == 200
    data = response.json()['data']
    assert data['pipeline'] == 'v3'
    assert data['evidence_text'] == '邮箱必须唯一'


async def test_validation_and_scope(client):
    assert (await client.post('/api/knowledge/inspect', json={'question': '   '})).status_code == 422
    assert (await client.post('/api/knowledge/inspect', json={'question': '你好', 'pipeline': 'legacy'})).status_code == 422
    assert (await client.post('/api/knowledge/inspect', json={'question': '你好', 'pipeline': 'v3'})).status_code == 422
    assert (await client.post('/api/knowledge/inspect', json={
        'question': '你好', 'project_id': str(uuid4())})).status_code == 404


async def test_service_failure_is_not_empty_result(client, monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError('private service details')
    monkeypatch.setattr('backend.rag_v3.inspect', broken)
    response = await client.post('/api/knowledge/inspect', json={'question': '公司规定'})
    assert response.status_code == 503
    assert 'private' not in response.text


@pytest.mark.parametrize('question,needed', [
    ('你好', False), ('谢谢', False), ('写一个排序函数', True), ('2+3=?', True),
    ('什么是 RAG', True), ('我们公司审批流程是什么', True),
    ('你好，成员 CSV 导入要求是什么', True), ('你好，报销标准是什么', True),
])
async def test_inspection_never_calls_llm(client, monkeypatch, question, needed):
    from backend.config import get_settings
    monkeypatch.setattr(get_settings(), 'model_mode', 'real')
    async def forbidden(*args, **kwargs):
        raise AssertionError('inspection must not call LLM API')
    monkeypatch.setattr('backend.intelligence.structured', forbidden)
    async def inspect(*args):
        return {'pipeline': 'v3', 'status': 'index_unavailable', 'chunks': [], 'evidence_text': ''}
    monkeypatch.setattr('backend.rag_v3.inspect', inspect)
    response = await client.post('/api/knowledge/inspect', json={'question': question})
    assert response.status_code == 200
    assert response.json()['data']['needs_rag'] is needed
    assert response.json()['data']['decision_mode'] == 'rules'
