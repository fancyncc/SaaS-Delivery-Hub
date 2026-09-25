from contextlib import aclosing
from uuid import uuid4

from sqlalchemy import select

from backend.db import SessionLocal
from backend.models import (
    KnowledgeDocument,
    Project,
    ProjectCollaboration,
    ProjectMembership,
    Tenant,
    TenantMembership,
    User,
)
from backend.rag_v3_models import V3Document
from tests.test_api import project, role_client


async def test_project_member_can_retry_failed_document(client):
    own = await project(client)
    other = await project(client)
    member = await role_client(client, own['id'], 'customer_contact', 'retry-member@example.com')
    viewer = await role_client(client, own['id'], 'viewer', 'retry-viewer@example.com')
    outsider = await role_client(client, other['id'], 'customer_contact', 'retry-outsider@example.com')
    async with aclosing(member), aclosing(viewer), aclosing(outsider):
        uploaded = await member.post('/api/knowledge/files', data={
            'project_id': own['id'], 'title': '项目索引失败资料', 'version': '1',
            'module': '项目资料', 'license': '项目内部使用'},
            files={'file': ('notes.txt', '项目成员提交的可检索文档。'.encode(), 'text/plain')})
        assert uploaded.status_code == 200, uploaded.text
        doc_id = uploaded.json()['data']['id']
        async with SessionLocal() as session:
            source = await session.scalar(select(V3Document).where(V3Document.origin_id == doc_id))
            source.phase, source.error = 'failed', 'PARSER_ERROR'
            await session.commit()
        assert (await member.get(f'/api/knowledge/{doc_id}')).json()['data']['can_reindex']
        assert not (await viewer.get(f'/api/knowledge/{doc_id}')).json()['data']['can_reindex']
        assert (await viewer.post(f'/api/knowledge/{doc_id}/v3-index')).status_code == 403
        assert (await outsider.post(f'/api/knowledge/{doc_id}/v3-index')).status_code == 404
        retried = await member.post(f'/api/knowledge/{doc_id}/v3-index')
        assert retried.status_code == 200, retried.text
        assert (await member.get(f'/api/knowledge/{doc_id}')).json()['data']['v3']['phase'] == 'pending'


async def test_member_documents_and_tasks(client):
    p = await project(client)
    member = await role_client(client, p['id'], 'customer_contact', 'contributor@example.com')
    approver = await role_client(client, p['id'], 'approver', 'task-approver@example.com')
    async with aclosing(member):
        me = (await member.get('/api/auth/me')).json()['data']
        doc = await member.post('/api/knowledge', json={
            'project_id': p['id'], 'title': '项目需求文档', 'version': 1, 'module': '需求资料',
            'source': '成员提交材料', 'license': '项目内部使用', 'body': '这是成员提交的项目需求文档，包含部门、角色权限以及验收标准。'})
        assert doc.status_code == 200, doc.text
        doc_id = doc.json()['data']['id']
        assert (await client.get(f'/api/knowledge/{doc_id}')).status_code == 200
        async with SessionLocal() as session:
            assert (await session.get(KnowledgeDocument, doc_id)).submitted_by == me['id']
        file = await member.post('/api/knowledge/files', data={
            'project_id': p['id'], 'title': '项目说明附件', 'version': '1', 'module': '项目资料', 'license': '项目内部使用'},
            files={'file': ('notes.txt', '成员提交的附件正文。'.encode(), 'text/plain')})
        assert file.status_code == 200, file.text
        base = f"/api/projects/{p['id']}/tasks"
        data = {'title': '收集需求', 'description': '整理各部门需求', 'assignee_id': me['id'], 'due_date': '2026-10-10'}
        first = await member.post(base, json=data)
        assert first.status_code == 200, first.text
        task = first.json()['data']
        assert (await member.post(base, json={'title': '准备培训'})).status_code == 200
        listing = (await client.get(base)).json()['data']
        assert len(listing['tasks']) == 2
        assert me['id'] in {m['id'] for m in listing['members']}
        # Status is server-owned: clients cannot manually mark work done.
        assert (await member.put(f"{base}/{task['id']}", json={**data, 'status':'done', 'expected_version':1})).status_code == 422
        started = await member.post(f"{base}/{task['id']}/actions/start", json={'expected_version':1})
        assert started.status_code == 200 and started.json()['data']['status'] == 'in_progress'
        assert (await member.post(f"{base}/{task['id']}/actions/submit_review",
                                  json={'expected_version':2})).status_code == 422
        evidence = await member.post('/api/knowledge/files', data={
            'project_id': p['id'], 'task_id':task['id'], 'title':'需求整理交付物', 'version':'1',
            'module':'任务交付', 'license':'项目内部使用'},
            files={'file':('requirements.md', '# 需求整理\n已完成部门需求整理。'.encode(), 'text/markdown')})
        assert evidence.status_code == 200, evidence.text
        submitted = await member.post(f"{base}/{task['id']}/actions/submit_review",
                                      json={'expected_version':2,'comment':'请审批需求整理结果'})
        assert submitted.status_code == 200 and submitted.json()['data']['status'] == 'pending_review'
        waiting = next(x for x in (await member.get(base)).json()['data']['tasks'] if x['id'] == task['id'])
        assert waiting['documents'][0]['title'] == '需求整理交付物'
        assert waiting['latest_review']['status'] == 'pending'
        assert (await member.put(f"{base}/{task['id']}", json={**data,'expected_version':3})).status_code == 409
        assert (await member.post(base, json={'title': '非法分配', 'assignee_id': str(uuid4())})).status_code == 422
        assert (await member.post(base, json={'title': '   '})).status_code == 422
        assert (await member.post('/api/knowledge', json={
            'title': '公司资料', 'version': 1, 'module': '通用资料', 'source': '成员提交',
            'license': '内部使用', 'body': '这是没有公司管理员权限的普通成员尝试提交的公司资料。'})).status_code == 403
    async with aclosing(approver):
        waiting = next(x for x in (await approver.get(base)).json()['data']['tasks'] if x['id'] == task['id'])
        review = waiting['latest_review']
        approved = await approver.post(f"{base}/{task['id']}/reviews/{review['id']}/decision",
                                       json={'decision':'approved','expected_version':waiting['version'],'comment':'交付物符合要求'})
        assert approved.status_code == 200, approved.text
        assert approved.json()['data']['status'] == 'done'


async def test_readonly_and_project_isolation(client):
    p = await project(client)
    other = await project(client)
    viewer = await role_client(client, p['id'], 'viewer', 'readonly@example.com')
    async with aclosing(viewer):
        base = f"/api/projects/{p['id']}/tasks"
        assert (await viewer.get(base)).status_code == 200
        assert (await viewer.post(base, json={'title': '越权写入'})).status_code == 403
        assert (await viewer.get(f"/api/projects/{other['id']}/tasks")).status_code == 404
        assert (await viewer.post('/api/knowledge', json={
            'project_id': p['id'], 'title': '只读提交', 'version': 1, 'module': '需求资料',
            'source': '成员提交材料', 'license': '项目内部使用', 'body': '这是只读成员提交的项目需求文档，应当被服务器拒绝提交。'})).status_code == 403
    task = (await client.post(f"/api/projects/{p['id']}/tasks", json={'title': '协作任务'})).json()['data']
    assert (await client.put(f"/api/projects/{other['id']}/tasks/{task['id']}", json={'title': '跨项目更新', 'expected_version': 1})).status_code == 404
    async with SessionLocal() as session:
        item = await session.get(Project, p['id'])
        item.lifecycle_status = 'completed'
        await session.commit()
    assert (await client.post(f"/api/projects/{p['id']}/tasks", json={'title': '结束后写入'})).status_code == 409


async def test_task_rejection_requires_other_approver_and_returns_to_work(client):
    p = await project(client)
    submitter = await role_client(client, p['id'], 'approver', 'review-submitter@example.com')
    reviewer = await role_client(client, p['id'], 'approver', 'reviewer@example.com')
    base = f"/api/projects/{p['id']}/tasks"
    async with aclosing(submitter):
        me = (await submitter.get('/api/auth/me')).json()['data']
        task = (await submitter.post(base, json={'title':'提交培训方案','assignee_id':me['id']})).json()['data']
        started = (await submitter.post(f"{base}/{task['id']}/actions/start", json={'expected_version':1})).json()['data']
        uploaded = await submitter.post('/api/knowledge', json={
            'project_id':p['id'], 'task_id':task['id'], 'title':'培训方案交付物', 'version':1,
            'module':'任务交付', 'source':'任务成员提交', 'license':'项目内部使用',
            'body':'这是用于任务审批的培训方案交付文档，包含培训对象、课程内容和验收方式。'})
        assert uploaded.status_code == 200, uploaded.text
        submitted = await submitter.post(f"{base}/{task['id']}/actions/submit_review",
                                         json={'expected_version':started['version'],'comment':'请审核'})
        assert submitted.status_code == 200
        waiting = next(x for x in (await submitter.get(base)).json()['data']['tasks'] if x['id']==task['id'])
        review_id = waiting['latest_review']['id']
        own = await submitter.post(f"{base}/{task['id']}/reviews/{review_id}/decision",
                                   json={'decision':'approved','expected_version':waiting['version']})
        assert own.status_code == 403
    async with aclosing(reviewer):
        waiting = next(x for x in (await reviewer.get(base)).json()['data']['tasks'] if x['id']==task['id'])
        rejected = await reviewer.post(f"{base}/{task['id']}/reviews/{review_id}/decision",
            json={'decision':'rejected','expected_version':waiting['version'],'comment':'请补充培训签到和测验标准'})
        assert rejected.status_code == 200 and rejected.json()['data']['status'] == 'changes_requested'
    # The assignee explicitly resumes work; rejection itself never marks the task complete.
    resumed = await client.post(f"{base}/{task['id']}/actions/start",
                                json={'expected_version':rejected.json()['data']['version']})
    assert resumed.status_code == 200 and resumed.json()['data']['status'] == 'in_progress'


async def test_revoked_member_loses_access(client):
    p = await project(client)
    member = await role_client(client, p['id'], 'customer_contact', 'revoked@example.com')
    async with aclosing(member):
        me = (await member.get('/api/auth/me')).json()['data']
        async with SessionLocal() as session:
            membership = await session.scalar(select(ProjectMembership).where(
                ProjectMembership.project_id == p['id'], ProjectMembership.user_id == me['id']))
            membership.status = 'revoked'
            await session.commit()
        assert (await member.get(f"/api/projects/{p['id']}/tasks")).status_code == 404


async def test_collaborating_company_shares_documents_and_tasks(client):
    from httpx import ASGITransport, AsyncClient

    from backend.main import app
    from backend.security import hash_password

    p = await project(client)
    owner = (await client.get('/api/auth/me')).json()['data']
    async with SessionLocal() as session:
        tenant = Tenant(name='协作公司', slug='task-collaborator')
        user = User(email='partner@example.com', display_name='协作成员', password_hash=hash_password('Partner12345'), account_type='customer')
        session.add_all([tenant, user])
        await session.flush()
        session.add(TenantMembership(tenant_id=tenant.id, user_id=user.id, role='tenant_member', company_role_code='company_member'))
        session.add(ProjectMembership(project_id=p['id'], tenant_id=tenant.id, user_id=user.id,
                                      project_role='customer_contact', primary_role_code='customer_contact', granted_by=owner['id']))
        collaboration = ProjectCollaboration(project_id=p['id'], owner_tenant_id=owner['tenant_id'],
                                             tenant_id=tenant.id, status='active', invited_by=owner['id'])
        session.add(collaboration)
        await session.commit()
        collaboration_id, user_id = collaboration.id, user.id
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as partner:
        login = await partner.post('/api/auth/login', json={'email':'partner@example.com','password':'Partner12345'})
        assert login.status_code == 200
        partner.headers['X-CSRF-Token'] = partner.cookies.get('saas_csrf')
        doc = await partner.post('/api/knowledge', json={
            'project_id': p['id'], 'title': '协作方资料', 'version': 1, 'module': '需求资料',
            'source': '协作成员提交', 'license': '项目内部使用', 'body': '这是协作公司提交的项目文档，项目所有有效成员都可以阅读。'})
        assert doc.status_code == 200, doc.text
        doc_id = doc.json()['data']['id']
        assert (await client.get(f'/api/knowledge/{doc_id}')).status_code == 200
        assert doc_id in {d['id'] for d in (await client.get('/api/knowledge')).json()['data']}
        task = await client.post(f"/api/projects/{p['id']}/tasks", json={'title':'跨公司协作', 'assignee_id':user_id})
        assert task.status_code == 200, task.text
        assert len((await partner.get(f"/api/projects/{p['id']}/tasks")).json()['data']['tasks']) == 1
        async with SessionLocal() as session:
            collaboration = await session.get(ProjectCollaboration, collaboration_id)
            collaboration.status = 'revoked'
            await session.commit()
        assert (await partner.get(f'/api/knowledge/{doc_id}')).status_code == 404
        assert (await partner.get(f"/api/projects/{p['id']}/tasks")).status_code == 404
        assert (await client.post(f"/api/projects/{p['id']}/tasks", json={'title':'失效协作分配', 'assignee_id':user_id})).status_code == 422
