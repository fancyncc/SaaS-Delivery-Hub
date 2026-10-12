import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from test_api import project, role_client

from backend import delivery
from backend.db import SessionLocal
from backend.models import AgentRun, Approval, ImportJob, Project, RemediationTask, SaaSMember
from backend.schemas import AcceptanceReport, GoLiveCheckResult, ImplementationGraphState


async def preparing(client, approvals_count=2):
    p = await project(client)
    response = await client.post(f"/api/projects/{p['id']}/runs", headers={"Idempotency-Key": str(uuid.uuid4())})
    run_id = response.json()["data"]["id"]
    own_id = (await client.get("/api/auth/me")).json()["data"]["id"]
    assigned = await client.post(f"/api/projects/{p['id']}/members", headers={"Idempotency-Key": str(uuid.uuid4())}, json={"user_id": own_id, "primary_role_code": "implementation_consultant"})
    assert assigned.status_code == 200
    approver = await role_client(client, p["id"], "approver", "delivery-approver@example.com")
    for _ in range(approvals_count):
        approval = next(a for a in (await approver.get(f"/api/approvals?run_id={run_id}")).json()["data"] if a["status"] == "pending")
        decided = await approver.post(f"/api/approvals/{approval['id']}/decision", headers={"Idempotency-Key": str(uuid.uuid4())}, json={"decision": "approved", "expected_version": approval["version"]})
        assert decided.status_code == 200
    return p, run_id, approver


async def test_material_preparation_and_real_validation(client):
    p, run_id, approver = await preparing(client)
    try:
        run = (await client.get(f"/api/runs/{run_id}")).json()["data"]
        assert run["status"] == "preparing_materials"
        assert "upload_csv" in run["allowed_actions"]
        assert (await client.post(f"/api/projects/{p['id']}/runs", headers={"Idempotency-Key": str(uuid.uuid4())})).status_code == 409
        response = await client.post("/api/imports/validate", headers={"Idempotency-Key": str(uuid.uuid4())}, json={"project_id": p["id"], "run_id": run_id, "csv_text": "name,email,department,role\nA,a@example.com,不存在,root"})
        assert not response.json()["data"]["valid"]
        assert len(response.json()["data"]["errors"]) == 2
    finally:
        await approver.aclose()


async def test_bound_receipt_real_import_and_failed_acceptance(client):
    p, run_id, approver = await preparing(client)
    try:
        response = await client.post("/api/imports/validate", headers={"Idempotency-Key": str(uuid.uuid4())}, json={"project_id": p["id"], "run_id": run_id, "csv_text": "name,email,department,role\nA,a@example.com,设计部,admin"})
        assert response.json()["data"]["valid"]
        job_id = response.json()["data"]["job_id"]
        # Once submitted for approval, replacement cannot change the approved material.
        replacement = await client.post("/api/imports/validate", headers={"Idempotency-Key": str(uuid.uuid4())}, json={"project_id": p["id"], "run_id": run_id, "csv_text": "name,email,department,role\nB,b@example.com,设计部,member"})
        assert replacement.status_code == 409
        approvals = (await approver.get(f"/api/approvals?run_id={run_id}")).json()["data"]
        approval = next(a for a in approvals if a["status"] == "pending")
        decided = await approver.post(f"/api/approvals/{approval['id']}/decision", headers={"Idempotency-Key": str(uuid.uuid4())}, json={"decision": "approved", "expected_version": approval["version"]})
        assert decided.json()["data"]["run_status"] == "blocked"  # 1 actual member != 80 required
        async with SessionLocal() as session:
            run = await session.get(AgentRun, run_id)
            job = await session.get(ImportJob, job_id)
            proj = await session.get(Project, p["id"])
            state = ImplementationGraphState.model_validate(run.state)
            assert job.result["successful"] == 1
            assert not state.acceptance_report.ready
            tasks = (await session.scalars(select(RemediationTask).where(RemediationTask.project_id == proj.id))).all()
            assert tasks and all(task.status == "open" for task in tasks)
            # Replaying the controlled tool with the same receipt cannot create duplicates.
            await delivery.execute_csv(session, proj, run, state, job, approval["id"])
            assert len((await session.scalars(select(SaaSMember))).all()) == 1
            job.source_hash = "tampered"
            with pytest.raises(HTTPException):
                await delivery.execute_csv(session, proj, run, state, job, approval["id"])
    finally:
        await approver.aclose()


async def test_remediation_is_resolved_by_latest_check_not_feedback(client):
    p, run_id, approver = await preparing(client)
    try:
        async with SessionLocal() as session:
            proj = await session.get(Project, p["id"])
            run = await session.get(AgentRun, run_id)
            failed = AcceptanceReport(ready=False, checks=[GoLiveCheckResult(name="成员数量", passed=False, details="1/80")])
            await delivery.sync_remediation(session, proj, run, failed)
            await delivery.sync_remediation(session, proj, run, failed)
            tasks = (await session.scalars(select(RemediationTask))).all()
            assert len(tasks) == 1 and tasks[0].status == "open"
            passed = AcceptanceReport(ready=True, checks=[GoLiveCheckResult(name="成员数量", passed=True, details="80/80")])
            await delivery.sync_remediation(session, proj, run, passed)
            assert tasks[0].status == "resolved"
            assert tasks[0].evidence["details"] == "80/80"
    finally:
        await approver.aclose()


async def test_cross_run_receipt_is_rejected(client):
    p, run_id, approver = await preparing(client)
    try:
        async with SessionLocal() as session:
            run = await session.get(AgentRun, run_id)
            proj = await session.get(Project, p["id"])
            state = ImplementationGraphState.model_validate(run.state)
            receipt = await session.scalar(select(Approval).where(Approval.run_id == run_id, Approval.kind == "configuration"))
            original = receipt.payload
            receipt.payload = {**original, "project_id": str(uuid.uuid4())}
            with pytest.raises(HTTPException):
                await delivery.apply_configuration(session, proj, run, state)
    finally:
        await approver.aclose()


async def test_configuration_revision_invalidates_old_approval(client):
    p, run_id, approver = await preparing(client, approvals_count=1)
    try:
        run = (await client.get(f"/api/runs/{run_id}")).json()["data"]
        old = next(a for a in (await approver.get(f"/api/approvals?run_id={run_id}")).json()["data"] if a["status"] == "pending")
        payload = {"expected_version": run["version"], "departments": ["设计部", "实施部"], "statuses": ["待办", "完成"], "templates": ["客户模板"], "custom_fields": ["交付编号"], "due_date_notifications": True}
        response = await client.patch(f"/api/runs/{run_id}/configuration", json=payload)
        assert response.status_code == 200, response.text
        assert (await client.patch(f"/api/runs/{run_id}/configuration", json=payload)).status_code == 409
        denied = await approver.post(f"/api/approvals/{old['id']}/decision", headers={"Idempotency-Key": str(uuid.uuid4())}, json={"decision": "approved", "expected_version": old["version"]})
        assert denied.status_code == 409
        new = next(a for a in (await approver.get(f"/api/approvals?run_id={run_id}")).json()["data"] if a["status"] == "pending")
        assert new["id"] != old["id"]
        decided = await approver.post(f"/api/approvals/{new['id']}/decision", headers={"Idempotency-Key": str(uuid.uuid4())}, json={"decision": "approved", "expected_version": new["version"]})
        assert decided.status_code == 200, decided.text
        data = (await client.get(f"/api/projects/{p['id']}/delivery")).json()["data"]
        assert data["configuration"]["custom_fields"] == ["交付编号"]
        assert data["configuration"]["departments"] == ["设计部", "实施部"]
        artifact = data["artifacts"][0]
        exported = await client.get(f"/api/artifacts/{artifact['id']}?format=json")
        assert exported.status_code == 200
        assert exported.json()["run_id"] == run_id
        assert exported.json()["checksum"] == artifact["checksum"]
        assert exported.json()["content"]
    finally:
        await approver.aclose()


async def test_failed_rows_retry_is_new_bound_idempotent_run(client):
    p, run_id, approver = await preparing(client)
    try:
        uploaded = await client.post("/api/imports/validate", headers={"Idempotency-Key": str(uuid.uuid4())}, json={"project_id": p["id"], "run_id": run_id, "csv_text": "name,email,department,role\nA,a@example.com,设计部,admin\nB,b@example.com,设计部,member"})
        job_id = uploaded.json()["data"]["job_id"]
        # Model a recovered partial execution: A committed, B failed, the run is blocked.
        async with SessionLocal() as session:
            job = await session.get(ImportJob, job_id)
            proj = await session.get(Project, p["id"])
            ws = await delivery.workspace(session, proj)
            session.add(SaaSMember(tenant_id=proj.tenant_id, workspace_id=ws.id, name="A", email="a@example.com", department="设计部", role="admin"))
            job.status = "partial_failed"
            job.result = {"successful": 1, "failed": 1, "rows": [{"row": 2, "email": "a@example.com", "status": "completed"}, {"row": 3, "email": "b@example.com", "status": "failed", "error": "临时故障"}]}
            run = await session.get(AgentRun, run_id)
            run.status = "failed"
            proj.lifecycle_status = "blocked"
            await session.commit()
        headers = {"Idempotency-Key": str(uuid.uuid4())}
        response = await client.post(f"/api/imports/{job_id}/retry-failed", headers=headers, json={})
        assert response.status_code == 200, response.text
        retry = response.json()["data"]
        assert retry["run_id"] != run_id
        duplicate = await client.post(f"/api/imports/{job_id}/retry-failed", headers=headers, json={})
        assert duplicate.json()["data"] == retry
        async with SessionLocal() as session:
            job = await session.get(ImportJob, retry["job_id"])
            assert job.run_id == retry["run_id"]
            assert job.validation["retry_of_job_id"] == job_id
            assert [r["email"] for r in delivery.csv_rows(job)] == ["b@example.com"]
            assert len((await session.scalars(select(SaaSMember))).all()) == 1
    finally:
        await approver.aclose()
