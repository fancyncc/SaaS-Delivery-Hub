"""Run only inside the isolated preproduction API container."""
import asyncio
import json
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from backend.chat_models import ChatConversation
from backend.config import get_settings
from backend.db import SessionLocal
from backend.models import AgentRun, KnowledgeDocument, Project, ProjectArtifact, Tenant, User
from backend.retrieval_sources import authorized_sources, backfill, process_sources, retrieve
from backend.retrieval_sources_models import RetrievalSource


async def main():
    assert make_url(get_settings().database_url).database == 'saas_preproduction'
    marker = uuid4().hex[:10]
    async with SessionLocal() as session:
        tenant = Tenant(name='M2 原创测试 '+marker, slug='m2-test-'+marker)
        user = User(display_name='M2 测试所有者', password_hash='disabled-test-account', email='m2-'+marker+'@example.invalid')
        session.add_all([tenant,user])
        await session.flush()
        await session.execute(text("SELECT set_config('app.current_tenant_id', :tenant, true)"), {'tenant':tenant.id})
        await session.execute(text("SELECT set_config('app.current_company_role', 'company_admin', true)"))
        project = Project(tenant_id=tenant.id,name='原创检索验收项目',customer_name='虚构测试公司',requirements_text='项目需求：成员导入必须校验邮箱格式和唯一性。')
        session.add(project)
        await session.flush()
        run = AgentRun(tenant_id=tenant.id,project_id=project.id,run_number=1,trace_id=uuid4().hex)
        session.add(run)
        await session.flush()
        session.add_all([
            KnowledgeDocument(tenant_id=tenant.id,title='原创测试功能说明',version=1,module='import',source='原创测试',license='测试',body='企业知识：成员导入必须校验邮箱格式和唯一性。'),
            ProjectArtifact(tenant_id=tenant.id,project_id=project.id,run_id=run.id,kind='test',title='原创测试交付物',version=1,checksum=uuid4().hex,content='交付验收：成员导入必须校验邮箱格式和唯一性。')])
        conversation = ChatConversation(tenant_id=tenant.id,user_id=user.id,project_id=project.id,documents=[{'id':str(uuid4()),'name':'原创私有测试.txt','text':'私有附件：成员导入必须校验邮箱格式和唯一性。'}])
        session.add(conversation)
        await session.flush()
        assert await backfill(session,tenant.id) == 4
        assert await backfill(session,tenant.id) == 4
        assert await process_sources(session,tenant.id) == 4
        sources = list(await session.scalars(select(RetrievalSource).where(RetrievalSource.tenant_id==tenant.id)))
        assert len(sources)==4 and all(s.phase=='ready' for s in sources), [(s.kind,s.phase,s.error_code) for s in sources]
        scope = dict(project_ids=[project.id],artifact_project_ids=[project.id],user_id=user.id,conversation_id=conversation.id)
        hits = await retrieve(session,tenant.id,'成员导入如何校验邮箱？',**scope)
        assert len(hits)==4, len(hits)
        limited = await retrieve(session,tenant.id,'成员导入如何校验邮箱？')
        assert len(limited)==1 and limited[0]['source']=='企业知识库'
        assert len(await authorized_sources(session,tenant.id,**{**scope,'user_id':'other-user'}))==3
        await session.commit()
        print(json.dumps({'test_tenant':tenant.id,'sources_ready':4,'real_retrieval_hits':len(hits),'company_only_hits':len(limited),'private_other_user_denied':True},ensure_ascii=False))

asyncio.run(main())
