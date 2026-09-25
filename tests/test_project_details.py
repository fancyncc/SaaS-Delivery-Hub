from uuid import uuid4

from backend.db import SessionLocal
from backend.models import AgentRun, Project
from tests.test_api import project_body


async def test_update_project_and_reject_stale_version(client):
    body = project_body()
    p = (await client.post('/api/projects', json=body, headers={'Idempotency-Key':str(uuid4())})).json()['data']
    payload = {**body, 'name':'新版实施项目', 'expected_version':p['version']}
    r = await client.patch(f"/api/projects/{p['id']}", json=payload)
    assert r.status_code == 200
    updated = (await client.get(f"/api/projects/{p['id']}")).json()['data']
    assert updated['name'] == payload['name']
    assert updated['document']['name'] == payload['name']
    assert (await client.patch(f"/api/projects/{p['id']}", json=payload)).status_code == 409


async def test_completed_and_active_projects_guarded(client):
    body = project_body()
    p = (await client.post('/api/projects', json=body, headers={'Idempotency-Key':str(uuid4())})).json()['data']
    async with SessionLocal() as session:
        row = await session.get(Project, p['id'])
        row.lifecycle_status = 'completed'
        await session.commit()
    payload = {**body, 'expected_version':p['version']}
    assert (await client.patch(f"/api/projects/{p['id']}", json=payload)).status_code == 409
    assert (await client.get(f"/api/projects/{p['id']}")).status_code == 200
    async with SessionLocal() as session:
        row = await session.get(Project, p['id'])
        row.lifecycle_status = 'in_progress'
        session.add(AgentRun(tenant_id=row.tenant_id, project_id=row.id, started_by=row.created_by, run_number=1, status='pending',trace_id=str(uuid4())))
        await session.commit()
    assert (await client.patch(f"/api/projects/{p['id']}", json=payload)).status_code == 409
