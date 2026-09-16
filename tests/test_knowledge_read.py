from sqlalchemy import select

from backend.db import SessionLocal
from backend.models import KnowledgeDocument, Project, Tenant


async def test_read_full_document_and_inactive_version(client):
    body = '# 完整内容\n\n' + '成员导入必须校验邮箱与角色。' * 40
    response = await client.post('/api/knowledge', json=dict(title='全文测试', version=1,
        module='import', source='原创测试', license='内部授权', body=body))
    identifier = response.json()['data']['id']
    read = await client.get(f'/api/knowledge/{identifier}')
    assert read.status_code == 200
    assert read.json()['data']['body'] == body
    await client.post(f'/api/knowledge/{identifier}/deactivate')
    assert (await client.get(f'/api/knowledge/{identifier}')).json()['data']['active'] is False


async def test_cannot_read_other_tenant_or_deleted_project(client):
    async with SessionLocal() as session:
        tenant = Tenant(name='其他公司', slug='private-other')
        session.add(tenant)
        await session.flush()
        other = KnowledgeDocument(tenant_id=tenant.id, title='私密资料', version=1,
            module='private', source='测试', license='内部', body='不可泄露')
        session.add(other)
        own = await session.scalar(select(Tenant).where(Tenant.slug == 'legacy-demo'))
        from backend.models import utcnow
        project = Project(tenant_id=own.id, name='删除项目', customer_name='测试', deleted_at=utcnow())
        session.add(project)
        await session.flush()
        hidden = KnowledgeDocument(tenant_id=own.id, project_id=project.id, title='删除项目资料',
            version=1, module='private', source='测试', license='内部', body='不可泄露')
        session.add(hidden)
        await session.commit()
        ids = [other.id, hidden.id]
    for identifier in ids:
        response = await client.get(f'/api/knowledge/{identifier}')
        assert response.status_code == 404
        assert '不可泄露' not in response.text
