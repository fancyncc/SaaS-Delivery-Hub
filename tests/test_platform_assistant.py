import json
import uuid

import pytest
from sqlalchemy import select

from backend import intelligence
from backend.chat import ChatAnswer
from backend.config import get_settings
from backend.db import SessionLocal
from backend.models import Project, ProjectMembership
from backend.platform_knowledge import ARTICLES, VERSION, api_reference, search_manual
from tests.test_api import project, role_client
from tests.test_chat import new_chat, send


@pytest.mark.parametrize('query,topic', [
    ('这个平台有哪些功能和入口', 'overview'),
    ('如何注册账号并创建公司空间', 'registration'),
    ('公司管理员和项目经理的权限角色有什么区别', 'roles'),
    ('创建项目的立项文书必填字段', 'project'),
    ('实施流程有几个节点和阶段', 'workflow'),
    ('为什么不能审批自己发起的请求', 'approval'),
    ('配置模板自定义字段和提醒支持什么', 'configuration'),
    ('CSV成员导入重复邮箱如何处理', 'imports'),
    ('批量邀请公司成员激活登录账号', 'company-members'),
    ('培训交付物下载和验收报告', 'delivery'),
    ('执行失败取消重试以及blocked恢复', 'recovery'),
    ('如何配置真实LLM模型和v2智能体', 'agent'),
    ('知识库向量索引重建与资料版本', 'knowledge'),
    ('聊天附件PDF和长期记忆限制', 'chat'),
    ('跨公司协作与限时支持授权', 'collaboration'),
    ('项目删除回收站恢复保留几天', 'recycle'),
    ('平台后台评测Trace监控', 'platform'),
    ('出现401 403 409报错如何排查', 'errors'),
])
def test_platform_topic_recall(query, topic):
    assert any(hit['id'].startswith(f'platform:{topic}:') for hit in search_manual(query))


def test_api_constraints_come_from_running_schema():
    from backend.main import app
    references = api_reference(app.openapi(), 'POST /api/chat/conversations/{identifier}/messages 接口请求格式')
    text = references[0]['text']
    assert 'expected_version' in text and 'request_id' in text
    assert '4000' in text
    unsafe = {'paths': {'/api/chat': {'post': {'requestBody': {'content': {'application/json': {'schema': {'type': 'string', 'default': 'SECRET-CANARY', 'example': 'SECRET-CANARY'}}}}}}}}
    assert 'SECRET-CANARY' not in str(api_reference(unsafe, '/api/chat 接口参数'))


async def test_platform_answer_without_customer_documents(client, monkeypatch):
    monkeypatch.setattr(get_settings(), 'model_mode', 'deterministic')
    chat = await new_chat(client)
    result = await send(client, chat, '项目删除回收站恢复保留几天？')
    assert result.status_code == 200
    message = result.json()['data']['messages'][-1]
    assert '30天' in message['answer']
    assert any(c['kind'] == 'platform_manual' for c in message['citations'])
    info = (await client.get('/api/chat/assistant')).json()['data']
    assert info['version'] == VERSION and len(info['knowledge_topics']) == len(ARTICLES)


async def test_live_state_changes_and_roles(client, monkeypatch):
    monkeypatch.setattr(get_settings(), 'model_mode', 'deterministic')
    p = await project(client)
    chat = await new_chat(client, p['id'])
    before = (await send(client, chat, '我的项目当前状态和权限是什么？')).json()['data']
    snapshot = next(c for c in before['messages'][-1]['citations'] if c['kind'] == 'runtime')
    assert json.loads(snapshot['text'])['projects'][0]['project_status'] == 'draft'
    await client.post(f"/api/projects/{p['id']}/runs", headers={'Idempotency-Key': str(uuid.uuid4())})
    after = (await send(client, before, '现在卡在哪一步？')).json()['data']
    snapshot = next(c for c in after['messages'][-1]['citations'] if c['kind'] == 'runtime')
    live = json.loads(snapshot['text'])['projects'][0]
    assert live['run']['status'] == 'waiting_approval'
    assert live['pending_approvals'][0]['can_decide'] is False
    assert live['pending_approvals'][0]['kind'] == 'plan'


async def test_runtime_does_not_leak_unassigned_projects(client):
    p = await project(client)
    member = await role_client(client, p['id'], 'viewer', 'platform-readonly@example.com')
    secret = await project(client)
    async with SessionLocal() as session:
        hidden = await session.get(Project, secret['id'])
        hidden.name = 'HIDDEN-CANARY-98371'
        await session.commit()
    try:
        chat = await new_chat(member)
        result = await send(member, chat, '我的项目当前状态和权限是什么？')
        assert 'HIDDEN-CANARY-98371' not in result.text
        live = next(c for c in result.json()['data']['messages'][-1]['citations'] if c['kind'] == 'runtime')
        assert json.loads(live['text'])['matched_projects'] == 1
        async with SessionLocal() as session:
            membership = await session.scalar(select(ProjectMembership).where(ProjectMembership.project_id == p['id'], ProjectMembership.user_id != p.get('created_by', ''), ProjectMembership.primary_role_code == 'viewer'))
            membership.status = 'revoked'
            await session.commit()
        result = await send(member, result.json()['data'], '现在我有哪些项目？')
        live = next(c for c in result.json()['data']['messages'][-1]['citations'] if c['kind'] == 'runtime')
        assert json.loads(live['text'])['matched_projects'] == 0
    finally:
        await member.aclose()


async def test_llm_receives_platform_identity_and_authoritative_sources(client, monkeypatch):
    monkeypatch.setattr(get_settings(), 'model_mode', 'real')
    captured = []

    async def fake(task, source, schema):
        captured.append((task, source))
        index, hit = next((i, h) for i, h in enumerate(source['evidence'], 1) if h['kind'] == 'platform_manual')
        return ChatAnswer(answer=f'项目回收站默认保留30天。[{index}]', citation_ids=[hit['id']])

    monkeypatch.setattr(intelligence, 'structured', fake)
    chat = await new_chat(client)
    result = await send(client, chat, '平台的项目回收站保留多久？')
    assert result.status_code == 200, result.text
    task, source = captured[0]
    assert '专属实施助手' in task
    assert '客户制度' in task and '实时完成结果' in task
    assert source['assistant_profile']['version'] == VERSION
    assert 'password' not in json.dumps(source['assistant_profile']).lower()
