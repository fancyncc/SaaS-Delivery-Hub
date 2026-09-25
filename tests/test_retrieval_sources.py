from sqlalchemy import func, select

from backend.db import SessionLocal
from backend.models import KnowledgeDocument, Tenant
from backend.retrieval_sources import (
    authorized_sources,
    index_source,
    process_sources,
    sync_knowledge,
)
from backend.retrieval_sources_models import RetrievalChunk


async def test_source_update_deletion_and_replay():
    async with SessionLocal() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == 'legacy-demo'))
        document = KnowledgeDocument(tenant_id=tenant.id, title='测试索引生命周期', version=1,
                                     module='import', source='原创测试', license='测试', body='导入必须校验邮箱。')
        session.add(document)
        await session.flush()
        source = await sync_knowledge(session, document)
        await index_source(session, source)
        await index_source(session, source)
        assert await session.scalar(select(func.count()).select_from(RetrievalChunk)) == 1
        assert source.phase == 'ready'
        assert await authorized_sources(session, tenant.id) == [source]
        assert await authorized_sources(session, 'other-tenant') == []
        document.body = '更新后的导入规则。'
        assert await authorized_sources(session, tenant.id) == []
        await sync_knowledge(session, document)
        assert source.phase == 'pending' and source.generation == 2
        await process_sources(session, tenant.id)
        assert source.phase == 'ready' and source.indexed_generation == 2
        document.active = False
        assert await authorized_sources(session, tenant.id) == []
        await process_sources(session, tenant.id)
        assert source.phase == 'inactive'
        assert await session.scalar(select(func.count()).select_from(RetrievalChunk)) == 0

async def test_private_attachment_requires_owner_conversation_and_project_scope():
    from backend.chat_models import ChatConversation
    from backend.models import Project, User
    from backend.retrieval_sources import sync_conversation

    async with SessionLocal() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == 'legacy-demo'))
        user = await session.scalar(select(User).where(User.email == 'company-admin@example.com'))
        project = Project(tenant_id=tenant.id, name='私有范围测试', customer_name='测试公司')
        session.add(project)
        await session.flush()
        conversation = ChatConversation(tenant_id=tenant.id, user_id=user.id, project_id=project.id,
                                        documents=[{'id': 'private-test', 'name': '私有.txt', 'text': '私有需求'}])
        session.add(conversation)
        await session.flush()
        await sync_conversation(session, conversation)
        scope = dict(user_id=user.id, conversation_id=conversation.id, project_ids=[project.id])
        assert len(await authorized_sources(session, tenant.id, **scope)) == 1
        assert await authorized_sources(session, tenant.id) == []
        assert await authorized_sources(session, tenant.id, **{**scope, 'user_id': 'someone-else'}) == []
        assert await authorized_sources(session, tenant.id, **{**scope, 'conversation_id': 'other'}) == []
        assert await authorized_sources(session, tenant.id, **{**scope, 'project_ids': []}) == []
        conversation.documents = []
        assert await authorized_sources(session, tenant.id, **scope) == []


async def test_index_failure_is_sanitized_and_stops_after_three_attempts(monkeypatch):
    import backend.retrieval_sources as sources

    async def broken(body):
        raise ValueError('secret document and token should not be recorded')

    monkeypatch.setattr(sources, 'document_chunks', broken)
    async with SessionLocal() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == 'legacy-demo'))
        document = KnowledgeDocument(tenant_id=tenant.id, title='测试失败', version=1, module='import',
                                     source='原创测试', license='测试', body='测试失败内容')
        session.add(document)
        await session.flush()
        source = await sync_knowledge(session, document)
        for _ in range(3):
            assert await process_sources(session, tenant.id) == 1
        assert source.attempts == 3 and source.phase == 'failed'
        assert source.error_code == 'INDEX_DEPENDENCY_FAILED'
        assert await process_sources(session, tenant.id) == 0
        assert await session.scalar(select(func.count()).select_from(RetrievalChunk)) == 0


async def test_stale_ready_index_rebuilds_without_uploading_again():
    from backend.knowledge import index_identity
    async with SessionLocal() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == 'legacy-demo'))
        document = KnowledgeDocument(tenant_id=tenant.id, title='旧索引自动恢复', version=1,
            module='test', source='内部规范', license='内部授权', body='售后工单属性包含状态和负责人。')
        session.add(document)
        await session.flush()
        source = await sync_knowledge(session, document)
        await index_source(session, source)
        source.index_identity = 'old-deployment-address'
        await session.flush()
        assert await process_sources(session, tenant.id) == 1
        assert source.phase == 'ready' and source.index_identity == index_identity()
        assert source.generation == source.indexed_generation == 2
        assert await session.scalar(select(func.count()).select_from(RetrievalChunk)) == 1
