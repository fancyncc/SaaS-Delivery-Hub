import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from test_api import project

from backend.config import get_settings
from backend.db import SessionLocal
from backend.models import AgentRun


async def test_database_rejects_second_active_run(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "execution_mode", "worker")
    p = await project(client)
    response = await client.post(f"/api/projects/{p['id']}/runs",
                                headers={"Idempotency-Key": str(uuid.uuid4())})
    assert response.status_code == 200
    async with SessionLocal() as session:
        original = await session.get(AgentRun, response.json()["data"]["id"])
        session.add(AgentRun(project_id=original.project_id, tenant_id=original.tenant_id,
                            run_number=2, trace_id="test", status="blocked"))
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()
