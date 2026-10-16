"""Read-only project review probes; mutations are confined to a NEW scratch SQLite DB."""
import os
import sys
from pathlib import Path
from uuid import uuid4

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / 'pyproject.toml').is_file())
sys.path.insert(0, str(ROOT))
scratch = ROOT / 'tmp' / f'audit-isolated-{uuid4().hex}.db'
os.environ.update(SAAS_ENV_FILE=str(ROOT / 'config/rag.mock.env.example'),
                 DATABASE_URL=f'sqlite+aiosqlite:///{scratch.as_posix()}',
                 REDIS_URL='redis://127.0.0.1:6399/15', MODEL_MODE='deterministic', RAG_MODE='mock')
import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
from sqlalchemy import event, select, text
from backend.db import engine, SessionLocal, bootstrap_identity
from backend.config import get_settings
from backend.main import app
from backend.models import Base, User, Tenant, TenantMembership, PasswordResetToken, Project, KnowledgeDocument, ProjectCollaboration
from backend.retrieval_sources_models import RetrievalBase
from backend.security import Principal, hash_password, token_hash

findings = []
def record(name, **data):
    findings.append({'probe': name, **data})
    print(json.dumps(findings[-1], ensure_ascii=False), flush=True)

async def main():
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
        await c.run_sync(RetrievalBase.metadata.create_all)
    await bootstrap_identity()
    async with SessionLocal() as s:
        tenant = await s.scalar(select(Tenant).where(Tenant.slug == 'legacy-demo'))
        user = User(email='review-victim@example.test', display_name='Review Victim', password_hash=hash_password('InitialReview123!'))
        s.add(user); await s.flush()
        s.add(TenantMembership(tenant_id=tenant.id, user_id=user.id, role='tenant_admin', company_role_code='company_admin'))
        await s.commit()
        uid, tid = user.id, tenant.id
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url='http://audit.local') as client:
        reset_urls = []
        for _ in range(2):
            r = await client.post('/api/auth/password/forgot', json={'email':'review-victim@example.test'})
            reset_urls.append(r.json()['data'].get('preview_url'))
        tokens = [parse_qs(urlsplit(url).query)['token'][0] for url in reset_urls]
        r = await client.post('/api/auth/password/reset', json={'token':tokens[1], 'password':'ResetByReview123!'})
        login = await client.post('/api/auth/login', json={'email':'review-victim@example.test','password':'ResetByReview123!'})
        record('anonymous_password_takeover', reset_url_exposed=bool(reset_urls[1]), reset_status=r.status_code, login_status=login.status_code)
        r = await client.post('/api/auth/password/reset', json={'token':tokens[0], 'password':'OldLinkStillWorks123!'})
        record('older_reset_token_survives_newer_password_reset', status=r.status_code)
        admin = await client.post('/api/auth/platform/login', json={'email':get_settings().bootstrap_admin_email,'password':get_settings().bootstrap_admin_password})
        protected = await client.get('/api/platform/users')
        record('forced_password_change_not_enforced', login_status=admin.status_code, must_change=admin.json()['data']['must_change_password'], sensitive_endpoint_status=protected.status_code)
        # Legitimate admin login from the scratch account; no production credentials.
        await client.post('/api/auth/login',json={'email':'review-victim@example.test','password':'OldLinkStillWorks123!'})
        client.headers['X-CSRF-Token'] = client.cookies.get('saas_csrf')
        async def fake_inspect(*args, **kwargs):
            return {'pipeline':'retrieval','status':'insufficient_relevance','chunks':[], 'evidence_text':''}
        import backend.knowledge_retrieval as kr
        original = kr.inspect; kr.inspect = fake_inspect
        statuses = [(await client.post('/api/knowledge/inspect',json={'question':'审查配额'})).status_code for _ in range(40)]
        kr.inspect = original
        record('inspection_has_no_user_rate_limit', requests=len(statuses), successful=statuses.count(200), rate_limited=statuses.count(429))
    principal = Principal(user_id=uid, subject='review-victim@example.test', email='review-victim@example.test', display_name='Review',
        tenant_id=tid, tenant_name='Review', account_type='customer',session_context='customer',role='tenant_admin',is_platform_admin=False,
        company_role_code='company_admin',platform_roles=(),session_id='scratch')
    # Scope mismatch: collaborative project documents are readable but filtered from retrieval.
    from backend.knowledge_index import register, identity
    from backend.knowledge_routes import read_document
    async with SessionLocal() as s:
        partner = Tenant(name='Review Partner', slug='review-partner')
        s.add(partner); await s.flush()
        project = Project(tenant_id=partner.id, name='Review shared project', customer_name='Review',requirements_text='review',created_by=uid)
        s.add(project); await s.flush()
        s.add(ProjectCollaboration(project_id=project.id,owner_tenant_id=partner.id,tenant_id=tid,status='active',invited_by=uid))
        doc = KnowledgeDocument(tenant_id=partner.id,project_id=project.id,title='Shared review evidence',version=1,module='test',source='review.md',license='internal',body='Shared authorized project evidence.')
        s.add(doc); await s.flush()
        source = await register(s,doc); source.phase='ready';source.identity=identity()
        await s.commit()
        response=await read_document(doc.id,user=principal,session=s)
        rows=(await s.execute(kr.visible_documents(tid,[project.id]))).all()
        record('collaborator_can_read_but_cannot_retrieve', readable=response['data']['id']==doc.id,retrieval_documents=len(rows))
    # Citation adapter drops the source locator returned by retrieval.
    import backend.chat as chat
    old_retrieve=chat.retrieve
    async def fake_retrieve(*args, **kwargs):
        return [{'id':'audit-evidence','title':'Review','text':'Evidence body','source':'review.pdf','kind':'customer_document',
                 'document_id':'origin-123','version':2,'heading':'Section','location':{'page':3},'source_chunk_ids':['unit-123']}]
    chat.retrieve=fake_retrieve
    async with SessionLocal() as s:
        # The owner has company knowledge so the chat knowledge path is entered.
        s.add(KnowledgeDocument(tenant_id=tid,title='Own audit knowledge',version=1,module='test',source='own.md',license='internal',body='Own evidence'))
        await s.commit()
        sources=await chat.evidence(s,SimpleNamespace(id='scratch',documents=[],project_id=None),principal,'审查来源定位',{})
        output=next(row for row in sources if row['id']=='audit-evidence')
        record('chat_citation_provenance_lost', remaining_fields=sorted(output),page_preserved='location' in output,document_id_preserved='document_id' in output)
    chat.retrieve=old_retrieve
    # Deterministic interleaving: upload passes the task state check, submission
    # freezes its document IDs, then the in-flight upload commits a new document.
    from starlette.requests import Request
    import backend.knowledge_routes as knowledge_routes
    from backend.project_task_routes import task_action, TaskAction
    from backend.models import ProjectTask, ProjectTaskDocument, ProjectTaskReview
    request = Request({'type':'http','method':'POST','path':'/audit','headers':[], 'client':('127.0.0.1',0)})
    async with SessionLocal() as s:
        p=Project(tenant_id=tid,name='Review task race',customer_name='Review',requirements_text='review',created_by=uid)
        s.add(p);await s.flush()
        task=ProjectTask(tenant_id=tid,project_id=p.id,title='Review task',created_by=uid,status='in_progress')
        s.add(task);await s.flush()
        first=KnowledgeDocument(tenant_id=tid,project_id=p.id,title='Initial review document',version=1,module='test',source='first.md',license='internal',body='The original submitted task document.')
        s.add(first);await s.flush()
        s.add(ProjectTaskDocument(tenant_id=tid,project_id=p.id,task_id=task.id,document_id=first.id,submitted_by=uid))
        await s.commit();project_id,task_id=p.id,task.id
    checked,resume=asyncio.Event(),asyncio.Event()
    old_authorize=knowledge_routes.authorize_submission
    async def paused_authorize(*args,**kwargs):
        result=await old_authorize(*args,**kwargs)
        checked.set();await resume.wait();return result
    knowledge_routes.authorize_submission=paused_authorize
    async with SessionLocal() as a,SessionLocal() as b:
        pending=asyncio.create_task(knowledge_routes.upload_document(
            knowledge_routes.KnowledgeUpload(project_id=project_id,task_id=task_id,title='Late review document',version=1,module='test',source='late.md',license='internal',body='A late uploaded document after the approval submission.'),
            request,user=principal,session=a))
        await asyncio.wait_for(checked.wait(),timeout=10)
        try:
            submitted=await task_action(project_id,task_id,'submit_review',TaskAction(expected_version=1),request,user=principal,session=b)
        finally:resume.set()
        late=await pending
        await b.rollback()
        review=await b.scalar(select(ProjectTaskReview).where(ProjectTaskReview.task_id==task_id))
        linked=list(await b.scalars(select(ProjectTaskDocument.document_id).where(ProjectTaskDocument.task_id==task_id)))
        record('task_document_freeze_race',submitted_status=submitted['data']['status'],frozen_document_count=len(review.document_ids),linked_document_count=len(linked),late_upload_succeeded=bool(late['data']['id']))
    knowledge_routes.authorize_submission=old_authorize
    # Enable real foreign-key checking only for a separately created cleanup case.
    async with engine.connect() as c:
        await c.execute(text('PRAGMA foreign_keys=ON')); await c.commit()
    async with SessionLocal() as s:
        await s.execute(text('PRAGMA foreign_keys=ON'))
        p=Project(tenant_id=tid,name='Expired audit project',customer_name='Review',requirements_text='review',created_by=uid,deleted_at=datetime.now(UTC)-timedelta(days=31))
        s.add(p);await s.flush()
        d=KnowledgeDocument(tenant_id=tid,project_id=p.id,title='Expired audit evidence',version=1,module='test',source='own.md',license='internal',body='Expired evidence')
        s.add(d);await s.flush();await register(s,d);await s.commit()
        try:
            from backend.cleanup import purge_expired_projects
            await purge_expired_projects(s)
            record('project_cleanup_with_knowledge',status='unexpected_success')
        except Exception as exc:
            await s.rollback()
            record('project_cleanup_with_knowledge',exception=type(exc).__name__,foreign_key_failure='FOREIGN KEY' in str(exc))
    # Live network probes never authenticate or submit a valid upload.
    try:
        async with httpx.AsyncClient(trust_env=False,timeout=15) as live:
            small=await live.post('http://localhost:8080/api/knowledge/files',content=b'x'*1024,headers={'Content-Type':'application/octet-stream'})
            large=await live.post('http://localhost:8080/api/knowledge/files',content=b'x'*(1024*1024+1),headers={'Content-Type':'application/octet-stream'})
            record('nginx_upload_limit',small_status=small.status_code,over_1_mib_status=large.status_code,over_1_mib_is_proxy_html='nginx' in large.text.lower())
    except httpx.HTTPError as exc:
        record('nginx_upload_limit',status='not_run',reason=type(exc).__name__)
    (ROOT/'tmp/project-audit-probes.json').write_text(json.dumps(findings,ensure_ascii=False,indent=2),encoding='utf8')
    await engine.dispose()

if __name__=='__main__':asyncio.run(main())
