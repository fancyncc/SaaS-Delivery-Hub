import json
import uuid

from backend.chat import retrieval_summary
from tests.test_api import project_body, role_client
from tests.test_chat import new_chat, send


async def test_project_documents_filtered_before_ranking_and_scope_guard(client, monkeypatch):
    from backend.config import get_settings
    monkeypatch.setattr(get_settings(), 'model_mode', 'deterministic')
    monkeypatch.setattr(get_settings(), 'embedding_model', '')
    projects = []
    for code in ('XL-104', 'XL-105'):
        result = await client.post('/api/projects', headers={'Idempotency-Key': str(uuid.uuid4())}, json={**project_body(), 'name': code + '｜独立项目'})
        projects.append(result.json()['data'])
    for index, scope in enumerate([None, projects[0]['id'], projects[1]['id']]):
        result = await client.post('/api/knowledge', json={'project_id': scope, 'title': f'隔离验收资料{index}', 'version': 1,
            'module': 'implementation', 'source': '测试资料', 'license': '内部测试授权', 'body': f'隔离验收要求：SCOPE-CANARY-{index}。仅在已授权范围内使用本资料的内容。'})
        assert result.status_code == 200, result.text
    chat = await new_chat(client, projects[1]['id'])
    result = await send(client, chat, '隔离验收要求是什么？')
    assert result.status_code == 200, result.text
    chat = result.json()['data']
    citations = str(chat['messages'][-1]['citations'])
    assert 'SCOPE-CANARY-0' in citations and 'SCOPE-CANARY-2' in citations
    assert 'SCOPE-CANARY-1' not in citations
    # A real model must not be called to answer out-of-scope questions either.
    monkeypatch.setattr(get_settings(), 'model_mode', 'real')
    result = await send(client, chat, 'XL-104完成了吗？')
    message = result.json()['data']['messages'][-1]
    assert message['mode'] == 'scope' and message['citations'] == []
    assert '未检索' in message['answer']
    monkeypatch.setattr(get_settings(), 'model_mode', 'deterministic')
    result = await send(client, result.json()['data'], '文件必须使用 UTF-8 编码吗？')
    assert result.json()['data']['messages'][-1]['mode'] != 'scope'
    member = await role_client(client, projects[1]['id'], 'viewer', 'scope-reader@example.com')
    try:
        result = await send(member, await new_chat(member), '隔离验收要求是什么？')
        assert 'SCOPE-CANARY-1' not in str(result.json())
        assert 'SCOPE-CANARY-2' in str(result.json())
        listing = (await member.get('/api/knowledge')).json()['data']
        assert all(d['project_id'] != projects[0]['id'] for d in listing)
    finally:
        await member.aclose()


def test_offline_json_is_evidence_only_and_status_is_readable():
    raw = json.dumps({'projects': [{'project': 'XL-104', 'project_status': 'in_progress', 'run': {'status': 'preparing_materials'}, 'actual_business_members': 0}]})
    answer = retrieval_summary('XL-104完成了吗', [{'kind': 'runtime', 'text': raw}])
    assert '等待成员材料' in answer and '已导入 0 名成员' in answer
    assert 'project_status' not in answer and '{' not in answer
    answer = retrieval_summary('材料内容是什么', [{'title': '资料.json', 'text': '{"secret_format": "raw payload"}'}])
    assert 'secret_format' not in answer and '{' not in answer
    answer = retrieval_summary('验收要求', [{'title': '实施材料', 'text': '": "40名成员", "notes": "测试内容"}'}])
    assert 'notes' not in answer and '测试内容' not in answer


async def test_new_topic_does_not_inherit_previous_project(client, monkeypatch):
    from backend import chat_routes
    captured = []
    async def fake_evidence(session, row, user, query, schema):
        captured.append(query)
        return []
    monkeypatch.setattr(chat_routes, 'evidence', fake_evidence)
    chat = (await send(client, await new_chat(client), 'XL-104现在完成了吗')).json()['data']
    await send(client, chat, 'XL-105验收要求是什么')
    assert captured[-1] == 'XL-105验收要求是什么'
