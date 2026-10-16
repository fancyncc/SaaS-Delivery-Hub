"""Opt-in security integration checks on a NEW disposable PostgreSQL database."""
import asyncio
import os
import secrets
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def prepare():
    url = make_url(os.environ['SECURITY_TEST_POSTGRES_URL'])
    if not (url.database or '').startswith('security_remediation_test') or url.host not in {'localhost', '127.0.0.1'}:
        raise RuntimeError('Only a local disposable security_remediation_test database is allowed')
    owner = url.set(drivername='postgresql+psycopg').render_as_string(hide_password=False)
    os.environ.update(SAAS_ENV_FILE=str(Path('config/rag.mock.env.example').resolve()), DATABASE_URL=owner,
        APP_ENV='test', MAIL_DEBUG='true', REDIS_URL='redis://127.0.0.1:6399/15')
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], check=True)
    role, password = 'security_app_' + secrets.token_hex(5), secrets.token_hex(32)
    with psycopg.connect(url.set(drivername='postgresql').render_as_string(hide_password=False), autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD {}').format(sql.Identifier(role), sql.Literal(password)))
        conn.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)))
        conn.execute(sql.SQL('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}').format(sql.Identifier(role)))
    os.environ['DATABASE_URL'] = url.set(drivername='postgresql+psycopg', username=role, password=password).render_as_string(hide_password=False)
    return owner


async def check(owner):
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import backend.knowledge_routes as knowledge_routes
    from backend.chat_models import ChatConversation
    from backend.db import SessionLocal, engine
    from backend.knowledge_index import identity, register
    from backend.knowledge_index_models import IndexedDocument, RetrievalUnit
    from backend.knowledge_retrieval import visible_documents
    from backend.main import app
    from backend.models import (
        KnowledgeDocument,
        PasswordResetToken,
        Project,
        ProjectCollaboration,
        ProjectMembership,
        ProjectTask,
        ProjectTaskDocument,
        Tenant,
        TenantMembership,
        User,
    )
    from backend.security import hash_password, token_hash

    owner_engine = create_async_engine(owner)
    factory = async_sessionmaker(owner_engine, expire_on_commit=False)
    async with factory() as session:
        first, partner = Tenant(name='Owner', slug='security-owner'), Tenant(name='Partner', slug='security-partner')
        user = User(email='owner@security.test', display_name='Owner', password_hash=hash_password('OwnerSecurity123'))
        collaborator = User(email='partner@security.test', display_name='Partner', password_hash=hash_password('PartnerSecurity123'))
        session.add_all([first, partner, user, collaborator])
        await session.flush()
        session.add_all([TenantMembership(tenant_id=first.id, user_id=user.id, role='tenant_admin', company_role_code='company_admin'),
            TenantMembership(tenant_id=partner.id, user_id=collaborator.id, role='tenant_member', company_role_code='company_member')])
        project = Project(tenant_id=first.id, name='Security checks', customer_name='Test', requirements_text='安全测试', created_by=user.id)
        session.add(project)
        await session.flush()
        collaboration = ProjectCollaboration(project_id=project.id, owner_tenant_id=first.id, tenant_id=partner.id,
            invited_by=user.id, status='active')
        session.add_all([collaboration, ProjectMembership(project_id=project.id, tenant_id=partner.id,
            user_id=collaborator.id, primary_role_code='customer_contact', project_role='customer_contact', granted_by=user.id)])
        doc = KnowledgeDocument(tenant_id=first.id, project_id=project.id, title='共享证据', version=1,
            module='test', source='notes.md', license='internal', body='共享项目成员迁移流程：校验、迁移、核对与验收。')
        private = KnowledgeDocument(tenant_id=first.id, title='企业私有证据', version=1,
            module='test', source='notes.md', license='internal', body='不能泄露的公司通用知识')
        session.add_all([doc, private])
        await session.flush()
        for item in (doc, private):
            source = await register(session, item)
            source.phase, source.identity = 'ready', identity()
            session.add(RetrievalUnit(id=source.id + ':0', tenant_id=first.id, document_id=source.id,
                node_id='test', ordinal=0, text=item.body, kind='paragraph', location={}, tokens=20, lexemes='test'))
        task = ProjectTask(tenant_id=first.id, project_id=project.id, title='Task race', created_by=user.id, status='in_progress')
        session.add(task)
        await session.flush()
        session.add(ProjectTaskDocument(tenant_id=first.id, project_id=project.id, task_id=task.id, document_id=doc.id, submitted_by=user.id))
        await session.commit()

    async with SessionLocal() as session:
        assert not list(await session.scalars(select(RetrievalUnit)))
        for key, value in {'app.current_tenant_id':partner.id, 'app.current_user_id':collaborator.id,
                           'app.current_company_role':'company_member'}.items():
            await session.execute(text("SELECT set_config(:key, :value, true)"), {'key':key, 'value':value})
        visible = (await session.execute(visible_documents(partner.id, [project.id]))).all()
        assert [item.id for _, item in visible] == [doc.id]
        assert [unit.text for unit in await session.scalars(select(RetrievalUnit))] == [doc.body]
        try:
            await session.execute(IndexedDocument.__table__.update().where(IndexedDocument.origin_id == doc.id).values(error='forbidden'))
            await session.commit()
        except Exception:
            await session.rollback()
        async with factory() as authority:
            assert not await authority.scalar(select(IndexedDocument.error).where(IndexedDocument.origin_id == doc.id))
    print('PASS: collaborative SQL/RLS reads, private tenant data hidden, foreign writes denied', flush=True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as a, AsyncClient(transport=transport, base_url='http://test') as b:
        for client in (a, b):
            login = await client.post('/api/auth/login', json={'email':user.email, 'password':'OwnerSecurity123'})
            assert login.status_code == 200, login.text
            client.headers['X-CSRF-Token'] = client.cookies.get('saas_csrf')
        checked, resume = asyncio.Event(), asyncio.Event()
        original = knowledge_routes.authorize_submission
        async def pause(*args, **kwargs):
            result = await original(*args, **kwargs)
            checked.set()
            await asyncio.wait_for(resume.wait(), 10)
            return result
        knowledge_routes.authorize_submission = pause
        try:
            upload = asyncio.create_task(a.post('/api/knowledge', json={'project_id':project.id, 'task_id':task.id,
                'title':'并发上传材料', 'version':1, 'module':'test', 'source':'late.md', 'license':'internal',
                'body':'上传过程中另一个请求提交审批，必须序列化并检测版本变化。'}))
            await asyncio.wait_for(checked.wait(), 10)
            submission = asyncio.create_task(b.post(f'/api/projects/{project.id}/tasks/{task.id}/actions/submit_review', json={'expected_version':1}))
            await asyncio.wait({submission}, timeout=0.2)
            assert not submission.done(), 'Task row lock must block concurrent review'
            resume.set()
            uploaded, reviewed = await asyncio.gather(upload, submission)
            assert uploaded.status_code == 200, uploaded.text
            assert reviewed.status_code == 409, reviewed.text
        finally:
            resume.set()
            knowledge_routes.authorize_submission = original
        print('PASS: real PostgreSQL upload/review interleaving cannot change frozen material', flush=True)

        # Two different valid reset links racing must still allow only one password change.
        tokens = [secrets.token_urlsafe(32), secrets.token_urlsafe(32)]
        async with factory() as session:
            session.add_all([PasswordResetToken(user_id=user.id, token_hash=token_hash(token),
                expires_at=datetime.now(UTC)+timedelta(hours=1)) for token in tokens])
            await session.commit()
        results = await asyncio.gather(*[client.post('/api/auth/password/reset',
            json={'token':token, 'password':f'ConcurrentSecurePassword{number}123'})
            for number, (client, token) in enumerate(zip((a,b), tokens, strict=True))])
        assert sorted(result.status_code for result in results) == [200,410], [r.text for r in results]
        print('PASS: concurrent reset tokens consumed once and all prior sessions revoked', flush=True)

    async with factory() as session:
        stored = await session.get(ProjectCollaboration, collaboration.id)
        stored.status = 'revoked'
        await session.commit()
    async with SessionLocal() as session:
        for key, value in {'app.current_tenant_id':partner.id, 'app.current_user_id':collaborator.id,
                           'app.current_company_role':'company_member'}.items():
            await session.execute(text("SELECT set_config(:key, :value, true)"), {'key':key, 'value':value})
        assert not (await session.execute(visible_documents(partner.id, [project.id]))).all()
        assert not list(await session.scalars(select(RetrievalUnit)))
    print('PASS: revoked collaboration loses document and unit visibility', flush=True)
    async with factory() as session:
        project_row = await session.get(Project, project.id)
        project_row.deleted_at = datetime.now(UTC) - timedelta(days=31)
        foreign_doc = KnowledgeDocument(tenant_id=partner.id, project_id=project.id, title='合作方历史资料',
            version=1, module='test', source='partner.md', license='internal', body='已过保留期的合作项目材料')
        session.add(foreign_doc)
        await session.flush()
        await register(session, foreign_doc)
        session.add(ChatConversation(tenant_id=partner.id, user_id=collaborator.id, project_id=project.id))
        await session.commit()
    from backend.cleanup import purge_expired_projects
    async with SessionLocal() as session:
        session.info['rls_context'] = {'app.current_tenant_id':first.id, 'app.current_user_id':user.id,
            'app.current_company_role':'company_admin'}
        visible_ids = {item.id for _, item in (await session.execute(visible_documents(first.id, [project.id]))).all()}
        assert doc.id not in visible_ids and foreign_doc.id not in visible_ids
        assert await purge_expired_projects(session, first.id) == 1
    async with factory() as session:
        assert await session.get(Project, project.id) is None
        assert await session.get(KnowledgeDocument, foreign_doc.id) is None
    print('PASS: expired owner project cleanup removes collaborator documents and private child rows', flush=True)
    await owner_engine.dispose()
    await engine.dispose()


if __name__ == '__main__':
    owner = prepare()
    if sys.platform == 'win32':
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(check(owner))
        finally:
            loop.close()
    else:
        asyncio.run(check(owner))
