from uuid import uuid4

import pytest


async def test_no_retrieval_for_greeting(client, monkeypatch):
    async def unexpected(*args, **kwargs):
        raise AssertionError("unnecessary retrieval")
    monkeypatch.setattr("backend.knowledge_routes.retrieve", unexpected)
    response = await client.post('/api/knowledge/inspect', json={'question': '你好'})
    assert response.status_code == 200
    assert response.json()['data']['status'] == 'not_needed'
    assert response.json()['data']['chunks'] == []


async def test_empty_library_does_not_return_demo_corpus(client):
    response = await client.post('/api/knowledge/inspect', json={'question': '成员 CSV 导入要求'})
    assert response.status_code == 200
    assert response.json()['data']['status'] == 'not_found'


async def test_returns_uploaded_chunks(client):
    uploaded = await client.post('/api/knowledge', json={
        'title': '成员导入规定', 'version': 1, 'module': 'import', 'source': '内部规范',
        'license': '内部授权', 'body': '成员导入必须提供姓名、邮箱、部门。邮箱必须唯一，重复邮箱需要修改后重新校验。'})
    assert uploaded.status_code == 200
    response = await client.post('/api/knowledge/inspect', json={'question': '成员导入规定'})
    data = response.json()['data']
    assert data['status'] == 'found'
    assert data['chunks'][0]['document_id'] == uploaded.json()['data']['id']
    assert '邮箱必须唯一' in data['chunks'][0]['text']


async def test_validation_and_scope(client):
    assert (await client.post('/api/knowledge/inspect', json={'question': '   '})).status_code == 422
    assert (await client.post('/api/knowledge/inspect', json={
        'question': '你好', 'project_id': str(uuid4())})).status_code == 404


async def test_service_failure_is_not_empty_result(client, monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError('private service details')
    monkeypatch.setattr('backend.knowledge_routes.retrieve', broken)
    response = await client.post('/api/knowledge/inspect', json={'question': '公司规定'})
    assert response.status_code == 503
    assert 'private' not in response.text


@pytest.mark.parametrize('question,needed', [
    ('写一个排序函数', False), ('你好', False), ('2+3=?', False),
    ('什么是 RAG', False), ('我们公司审批流程是什么', True),
    ('你好，成员 CSV 导入要求是什么', True), ('你好，报销标准是什么', True),
])
async def test_inspection_never_calls_llm(client, monkeypatch, question, needed):
    from backend.config import get_settings
    monkeypatch.setattr(get_settings(), 'model_mode', 'real')
    async def forbidden(*args, **kwargs):
        raise AssertionError('inspection must not call LLM API')
    monkeypatch.setattr('backend.intelligence.structured', forbidden)
    response = await client.post('/api/knowledge/inspect', json={'question': question})
    assert response.status_code == 200
    assert response.json()['data']['needs_rag'] is needed
    assert response.json()['data']['decision_mode'] == 'rules'
