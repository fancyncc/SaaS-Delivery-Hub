from contextlib import aclosing
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, text

from backend.config import Settings, get_settings
from backend.db import SessionLocal
from backend.models import AuthSession, CleanupOutbox, KnowledgeDocument, User
from tests.mail_helpers import mail_link
from tests.test_api import project, role_client


async def test_public_reset_never_returns_credentials_and_invalidates_all_links(client):
    first = await client.post('/api/auth/password/forgot', json={'email': 'company-admin@example.com'})
    first_token = parse_qs(urlsplit(await mail_link('password-reset')).query)['token'][0]
    second = await client.post('/api/auth/password/forgot', json={'email': 'company-admin@example.com'})
    second_token = parse_qs(urlsplit(await mail_link('password-reset')).query)['token'][0]
    unknown = await client.post('/api/auth/password/forgot', json={'email': 'unknown@example.test'})
    assert first.json() == second.json() == unknown.json()
    assert 'preview_url' not in str(first.json()) and first_token not in first.text
    reset = await client.post('/api/auth/password/reset', json={'token': second_token, 'password': 'NewSecurePassword123'})
    assert reset.status_code == 200
    for token in (first_token, second_token):
        assert (await client.post('/api/auth/password/reset', json={'token': token, 'password': 'OldLinkMustFail123'})).status_code == 410
    assert (await client.get('/api/auth/me')).status_code == 401


async def test_chat_preserves_citation_metadata_and_original_text(client, monkeypatch):
    from uuid import uuid4
    source = {'id': 'unit-1', 'document_id': str(uuid4()), 'title': '审批原文', 'source': 'guide.pdf',
        'text': '成员迁移须先审批。' * 500, 'version': 2, 'heading': '迁移审批',
        'location': {'page': 3}, 'source_chunk_ids': ['unit-1'], 'kind': 'customer_document'}
    async def retrieve(*args, **kwargs):
        assert kwargs['knowledge_only'] is True
        return [source]
    monkeypatch.setattr('backend.chat.retrieve', retrieve)
    chat = (await client.post('/api/chat/conversations', json={})).json()['data']
    response = await client.post(f"/api/chat/conversations/{chat['id']}/messages", json={
        'question': '成员迁移须满足什么审批要求？', 'expected_version': chat['version'], 'request_id': str(uuid4())})
    assert response.status_code == 200, response.text
    citation = next(c for c in response.json()['data']['messages'][0]['citations'] if c['id'] == source['id'])
    for key in ('document_id', 'version', 'heading', 'location', 'source_chunk_ids', 'text'):
        assert citation[key] == source[key]


async def test_forced_password_change_is_enforced_and_rotates_sessions(platform_client):
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.email == 'admin@example.com'))
        user.must_change_password = True
        await session.commit()
    old_cookie = platform_client.cookies.get('saas_session')
    assert (await platform_client.get('/api/auth/me')).json()['data']['must_change_password']
    assert (await platform_client.get('/api/platform/users')).status_code == 403
    wrong = await platform_client.post('/api/auth/password/change', json={'current_password': 'Wrong123456', 'password': 'SecureAdminPassword123'})
    assert wrong.status_code == 400
    changed = await platform_client.post('/api/auth/password/change', json={'current_password': 'ChangeMe123!', 'password': 'SecureAdminPassword123'})
    assert changed.status_code == 200, changed.text
    platform_client.headers['X-CSRF-Token'] = platform_client.cookies.get('saas_csrf')
    assert platform_client.cookies.get('saas_session') != old_cookie
    assert not (await platform_client.get('/api/auth/me')).json()['data']['must_change_password']
    assert (await platform_client.get('/api/platform/users')).status_code == 200
    async with SessionLocal() as session:
        sessions = list(await session.scalars(select(AuthSession).where(AuthSession.user_id == user.id)))
        assert sum(s.revoked_at is None for s in sessions) == 1


def test_production_configuration_rejects_unsafe_defaults():
    with pytest.raises(ValidationError, match='Unsafe production configuration'):
        Settings(_env_file=None, app_env='production', cookie_secure=False)


async def test_inspection_budget_rejects_before_model(client, monkeypatch):
    calls = []
    async def inspect(*args, **kwargs):
        calls.append(1)
        return {'status': 'insufficient_relevance', 'chunks': [], 'evidence_text': ''}
    monkeypatch.setattr('backend.knowledge_retrieval.inspect', inspect)
    responses = [await client.post('/api/knowledge/inspect', json={'question': '触发回滚的情况有哪些'}) for _ in range(13)]
    assert len(calls) == 12
    assert responses[-1].status_code == 429 and responses[-1].headers['Retry-After']


async def test_concurrency_leases_release_on_exception(client):
    from types import SimpleNamespace

    from backend.resource_limits import work_budget
    me = (await client.get('/api/auth/me')).json()['data']
    user = SimpleNamespace(user_id=me['id'], tenant_id=me['tenant_id'])
    with pytest.raises(ValueError):
        async with work_budget(user, 'lease-test'), work_budget(user, 'lease-test'):
            with pytest.raises(HTTPException) as error:
                async with work_budget(user, 'lease-test'):
                    pytest.fail('Third lease must be denied')
            assert error.value.status_code == 429
            raise ValueError('Simulated parser failure')
    async with work_budget(user, 'lease-test'):
        pass


async def test_rate_limit_service_failure_is_closed_in_production(monkeypatch):
    from backend.rate_limit import enforce_rate_limit
    monkeypatch.setattr(get_settings(), 'app_env', 'production')
    with pytest.raises(HTTPException) as error:
        await enforce_rate_limit('production-failure', 1, 60)
    assert error.value.status_code == 503


async def test_pending_parse_cannot_be_submitted_and_snapshot_detects_change(client):
    p = await project(client)
    reviewer = await role_client(client, p['id'], 'approver', 'snapshot-reviewer@example.com')
    base = f"/api/projects/{p['id']}/tasks"
    task = (await client.post(base, json={'title': '审批材料快照'})).json()['data']
    await client.post(f"{base}/{task['id']}/actions/start", json={'expected_version': 1})
    uploaded = await client.post('/api/knowledge', json={'project_id': p['id'], 'task_id': task['id'],
        'title': '冻结审批材料', 'version': 1, 'module': '测试资料', 'source': 'approval.md', 'license': 'internal',
        'body': '这是审批时需要冻结的项目材料，包含审批范围和验收要求。'})
    assert uploaded.status_code == 200, uploaded.text
    submitted = await client.post(f"{base}/{task['id']}/actions/submit_review", json={'expected_version': 3})
    assert submitted.status_code == 200, submitted.text
    task = (await client.get(base)).json()['data']['tasks'][0]
    assert task['latest_review']['documents'][0]['body']
    assert (await client.post('/api/knowledge', json={'project_id': p['id'], 'task_id': task['id'],
        'title': '禁止追加材料', 'version': 1, 'module': '测试资料', 'source': 'late.md', 'license': 'internal',
        'body': '审批期间不得追加任何任务材料，服务端必须拒绝本次提交。'})).status_code == 409
    async with SessionLocal() as session:
        doc = await session.get(KnowledgeDocument, uploaded.json()['data']['id'])
        doc.body += ' Simulated change'
        await session.commit()
    async with aclosing(reviewer):
        result = await reviewer.post(f"{base}/{task['id']}/reviews/{task['latest_review']['id']}/decision",
            json={'decision': 'approved', 'expected_version': task['version']})
        assert result.status_code == 409


async def test_physical_cleanup_includes_indexes_with_real_foreign_keys(client):
    from backend.cleanup import drain_cleanup, purge_expired_projects
    from backend.knowledge_index import register
    from backend.knowledge_index_models import IndexedDocument
    from backend.models import Project
    p = await project(client)
    async with SessionLocal() as session:
        await session.execute(text('PRAGMA foreign_keys=ON'))
        assert await session.scalar(text('PRAGMA foreign_keys')) == 1
        owner = await session.get(Project, p['id'])
        owner.deleted_at = datetime.now(UTC) - timedelta(days=31)
        doc = KnowledgeDocument(tenant_id=owner.tenant_id, project_id=owner.id, title='待清理文档',
            version=1, module='test', source='notes.md', license='internal', body='清理对应文档与派生索引')
        session.add(doc)
        await session.flush()
        source = await register(session, doc)
        await session.commit()
        try:
            assert await purge_expired_projects(session) == 1
            assert await session.get(IndexedDocument, source.id) is None
            assert await session.scalar(select(KnowledgeDocument.id).where(KnowledgeDocument.id == doc.id)) is None
            assert len(list(await session.scalars(select(CleanupOutbox)))) == 1
            assert await drain_cleanup(session) == 1
        finally:
            await session.rollback()
            await session.execute(text('PRAGMA foreign_keys=OFF'))
            await session.commit()


async def test_external_cleanup_partial_failure_retains_retryable_tombstone(client, monkeypatch):
    from contextlib import asynccontextmanager

    import httpx

    from backend.cleanup import drain_cleanup, purge_expired_projects
    from backend.knowledge_index import register
    from backend.models import Project

    p = await project(client)
    partial = [True]
    def handler(request):
        return httpx.Response(200, json={'failures': [{'reason': 'temporary failure'}] if partial[0] else [],
                                        'timed_out': False})
    @asynccontextmanager
    async def external_client():
        async with httpx.AsyncClient(base_url='http://search.test', transport=httpx.MockTransport(handler)) as value:
            yield value
    monkeypatch.setattr('backend.lexical_index.client', external_client)
    monkeypatch.setattr(get_settings(), 'opensearch_url', 'http://search.test')
    async with SessionLocal() as session:
        owner = await session.get(Project, p['id'])
        owner.deleted_at = datetime.now(UTC) - timedelta(days=31)
        doc = KnowledgeDocument(tenant_id=owner.tenant_id, project_id=owner.id, title='需重试清理的索引',
            version=1, module='test', source='notes.md', license='internal', body='清理失败后保留重试凭据')
        session.add(doc)
        await session.flush()
        await register(session, doc)
        await session.commit()
        await purge_expired_projects(session)
        assert await drain_cleanup(session) == 1
        tombstone = await session.scalar(select(CleanupOutbox))
        assert tombstone.status == 'pending' and tombstone.attempts == 1 and tombstone.next_attempt > 0
        assert await session.scalar(select(KnowledgeDocument.id).where(KnowledgeDocument.id == doc.id)) is None
        assert await drain_cleanup(session) == 0
        partial[0] = False
        tombstone.next_attempt = 0
        await session.commit()
        assert await drain_cleanup(session) == 1
        assert tombstone.status == 'done'
