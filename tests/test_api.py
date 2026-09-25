import uuid
from urllib.parse import parse_qs, urlparse

from httpx import ASGITransport, AsyncClient

from backend.main import app


def project_body():
    return {
        "name": "星河实施",
        "customer_name": "星河设计",
        "customer_contact": "林岚",
        "contact_email": "linlan@example.com",
        "employee_count": 80,
        "target_go_live_date": "2026-10-15",
        "departments": ["设计部", "市场部", "财务部"],
        "requirements_text": "成员部门角色权限，CSV导入，项目模板状态流程，上线前完成管理员和成员培训。",
        "migration_scope": "导入 80 名成员。",
        "acceptance_criteria": "成员导入完成且权限抽查通过。",
    }


async def project(client):
    response = await client.post(
        "/api/projects",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json=project_body(),
    )
    assert response.status_code == 200
    return response.json()["data"]


async def role_client(client, project_id: str, role: str, email: str) -> AsyncClient:
    tenant_id = (await client.get("/api/auth/me")).json()["data"]["tenant_id"]
    invited = await client.post(
        "/api/company/invitations",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={"email": email, "company_role": "tenant_member", "tenant_id": tenant_id},
    )
    token = parse_qs(urlparse(invited.json()["data"]["invitation_url"]).query)["token"][0]
    member = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    accepted = await member.post(
        f"/api/auth/invitations/{token}/accept",
        json={"display_name": role, "password": "RoleMember123"},
    )
    assert accepted.status_code == 200
    member.headers["X-CSRF-Token"] = member.cookies.get("saas_csrf")
    member_id = (await member.get("/api/auth/me")).json()["data"]["id"]
    assigned = await client.post(
        f"/api/projects/{project_id}/members",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={"user_id": member_id, "primary_role_code": role},
    )
    assert assigned.status_code == 200
    return member


async def test_project_idempotency(client):
    key = str(uuid.uuid4())
    body = project_body()
    first = await client.post("/api/projects", headers={"Idempotency-Key": key}, json=body)
    second = await client.post("/api/projects", headers={"Idempotency-Key": key}, json=body)
    assert first.json()["data"]["id"] == second.json()["data"]["id"]


async def test_workflow_pauses_and_resumes_at_approval(client):
    p = await project(client)
    started = await client.post(f"/api/projects/{p['id']}/runs", headers={"Idempotency-Key": str(uuid.uuid4())})
    run = started.json()["data"]
    assert run["status"] == "waiting_approval" and run["current_node"] == "plan_approval"
    tasks = (await client.get("/api/tasks")).json()["data"]
    task = next(item for item in tasks if item["run_id"] == run["id"])
    assert task["project_name"] == p["name"]
    assert task["run_number"] == run["run_number"]
    assert task["current_node"] == "plan_approval"
    approvals = (await client.get("/api/approvals")).json()["data"]
    approver = await role_client(client, p["id"], "approver", "approver-1@example.com")
    try:
        decided = await approver.post(f"/api/approvals/{approvals[0]['id']}/decision", headers={"Idempotency-Key": str(uuid.uuid4())}, json={"decision": "approved", "expected_version": approvals[0]["version"]})
        assert decided.status_code == 200
        assert decided.json()["data"]["current_node"] == "configuration_approval"
    finally:
        await approver.aclose()


async def test_company_admin_can_read_run_but_cannot_decide_without_approver_role(client):
    created = await project(client)
    started = await client.post(
        f"/api/projects/{created['id']}/runs",
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    run = started.json()["data"]
    tenant_id = (await client.get("/api/auth/me")).json()["data"]["tenant_id"]
    invitation = await client.post(
        "/api/company/invitations",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={"email": "read-admin@example.com", "company_role": "tenant_admin", "tenant_id": tenant_id},
    )
    token = parse_qs(urlparse(invitation.json()["data"]["invitation_url"]).query)["token"][0]
    admin = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        accepted = await admin.post(
            f"/api/auth/invitations/{token}/accept",
            json={"display_name": "只读公司管理员", "password": "ReadAdmin123"},
        )
        assert accepted.status_code == 200
        admin.headers["X-CSRF-Token"] = admin.cookies.get("saas_csrf")

        projects = (await admin.get("/api/projects")).json()["data"]
        summary = next(item for item in projects if item["id"] == created["id"])
        assert summary["latest_run"]["id"] == run["id"]
        assert (await admin.get(f"/api/runs/{run['id']}")).status_code == 200
        approvals = (await admin.get(f"/api/approvals?run_id={run['id']}")).json()["data"]
        assert len(approvals) == 1

        denied = await admin.post(
            f"/api/approvals/{approvals[0]['id']}/decision",
            headers={"Idempotency-Key": str(uuid.uuid4())},
            json={"decision": "approved", "expected_version": approvals[0]["version"]},
        )
        assert denied.status_code == 403
    finally:
        await admin.aclose()


async def test_invalid_csv_cannot_execute(client):
    p = await project(client)
    started = await client.post(f"/api/projects/{p['id']}/runs", headers={"Idempotency-Key": str(uuid.uuid4())})
    run_id = started.json()["data"]["id"]
    approver = await role_client(client, p["id"], "approver", "csv-approver@example.com")
    for _ in range(2):
        item = next(a for a in (await approver.get(f"/api/approvals?run_id={run_id}")).json()["data"] if a["status"] == "pending")
        assert (await approver.post(f"/api/approvals/{item['id']}/decision", headers={"Idempotency-Key": str(uuid.uuid4())}, json={"decision": "approved", "expected_version": item["version"]})).status_code == 200
    await approver.aclose()
    consultant = await role_client(client, p["id"], "implementation_consultant", "consultant-1@example.com")
    try:
        validated = await consultant.post("/api/imports/validate", headers={"Idempotency-Key": str(uuid.uuid4())}, json={"project_id": p["id"], "run_id": run_id, "csv_text": "name,email\nA,bad"})
        job = validated.json()["data"]
        assert job["valid"] is False
        executed = await consultant.post(f"/api/imports/{job['job_id']}/execute", headers={"Idempotency-Key": str(uuid.uuid4())})
        assert executed.status_code in {403, 409}
    finally:
        await consultant.aclose()


async def test_write_requires_idempotency_key(client):
    response = await client.post("/api/projects", json=project_body())
    assert response.status_code == 400


async def test_csrf_failure_returns_json_403_instead_of_server_error(client):
    csrf_token = client.headers.pop("X-CSRF-Token")
    try:
        response = await client.post(
            "/api/projects",
            headers={"Idempotency-Key": str(uuid.uuid4())},
            json=project_body(),
        )
    finally:
        client.headers["X-CSRF-Token"] = csrf_token
    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF 校验失败"}


async def test_rejected_approval_marks_project_and_preserves_reason(client):
    created = await project(client)
    started = await client.post(
        f"/api/projects/{created['id']}/runs",
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    run_id = started.json()["data"]["id"]
    approvals = (await client.get(f"/api/approvals?run_id={run_id}")).json()["data"]
    approver = await role_client(client, created["id"], "approver", "approver-2@example.com")
    response = await approver.post(
        f"/api/approvals/{approvals[0]['id']}/decision",
        headers={
            "Idempotency-Key": str(uuid.uuid4()),
        },
        json={"decision": "rejected", "comment": "计划缺少数据迁移回滚方案", "expected_version": approvals[0]["version"]},
    )
    assert response.status_code == 200
    await approver.aclose()
    summary = (await client.get(f"/api/projects/{created['id']}")).json()["data"]
    assert summary["status"] == "blocked"
    assert summary["lifecycle_status"] == "blocked"
    assert summary["execution_status"] == "failed"
    assert summary["latest_approval"]["comment"] == "计划缺少数据迁移回滚方案"
    assert summary["can_start"] is True
    retried = await client.post(
        f"/api/projects/{created['id']}/runs",
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert retried.status_code == 200
    assert retried.json()["data"]["run_number"] == 2
    assert retried.json()["data"]["retry_of_run_id"] == run_id


async def test_generic_required_requirements_continue_to_plan_approval(client):
    body = project_body()
    body["requirements_text"] = "希望十月前完成系统上线，目前业务范围和实施细节都需要后续调研确认。"
    created_response = await client.post(
        "/api/projects",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json=body,
    )
    created = created_response.json()["data"]
    started = await client.post(
        f"/api/projects/{created['id']}/runs",
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert started.json()["data"]["status"] == "waiting_approval"
    assert started.json()["data"]["current_node"] == "plan_approval"
    summary = (await client.get(f"/api/projects/{created['id']}")).json()["data"]
    assert summary["status"] == "in_progress"
    assert summary["execution_status"] == "waiting_approval"
    assert summary["can_start"] is False
    run = (await client.get(f"/api/runs/{started.json()['data']['id']}")).json()["data"]
    assert run["state"]["requirements"] == [{
        "category": "general",
        "statement": body["requirements_text"],
        "priority": "medium",
        "source": "customer_document",
    }]


async def test_requirements_cannot_be_only_whitespace(client):
    body = project_body()
    body["requirements_text"] = " " * 20
    response = await client.post(
        "/api/projects",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json=body,
    )
    assert response.status_code == 422


async def test_project_delete_removes_run_and_is_idempotent(client):
    created = await project(client)
    started = await client.post(
        f"/api/projects/{created['id']}/runs",
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    run_id = started.json()["data"]["id"]
    key = str(uuid.uuid4())
    deleted = await client.delete(
        f"/api/projects/{created['id']}",
        headers={"Idempotency-Key": key},
    )
    repeated = await client.delete(
        f"/api/projects/{created['id']}",
        headers={"Idempotency-Key": key},
    )
    assert deleted.status_code == 200
    assert repeated.status_code == 200
    assert deleted.json()["data"] == repeated.json()["data"]
    assert (await client.get(f"/api/projects/{created['id']}")).status_code == 404
    # Soft deletion hides the project but preserves its execution history for recovery/audit.
    assert (await client.get(f"/api/runs/{run_id}")).status_code == 200


async def test_completed_project_cannot_start_again(client):
    created = await project(client)
    started = await client.post(
        f"/api/projects/{created['id']}/runs",
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    run_id = started.json()["data"]["id"]
    approver = await role_client(client, created["id"], "approver", "approver-3@example.com")
    consultant = await role_client(client, created["id"], "implementation_consultant", "delivery-consultant@example.com")
    try:
      for _ in range(4):
        current_run = (await client.get(f"/api/runs/{run_id}")).json()["data"]
        if current_run["status"] == "preparing_materials":
            csv_text = "name,email,department,role\n" + "\n".join(f"成员{i},member{i}@example.com,设计部,{'admin' if i == 0 else 'member'}" for i in range(80))
            uploaded = await consultant.post("/api/imports/validate", headers={"Idempotency-Key": str(uuid.uuid4())}, json={"project_id": created["id"], "run_id": run_id, "csv_text": csv_text})
            assert uploaded.status_code == 200 and uploaded.json()["data"]["valid"]
        approvals = (await approver.get(f"/api/approvals?run_id={run_id}")).json()["data"]
        pending = next(item for item in approvals if item["status"] == "pending")
        decision = await approver.post(
            f"/api/approvals/{pending['id']}/decision",
            headers={
                "Idempotency-Key": str(uuid.uuid4()),
            },
            json={"decision": "approved", "comment": "同意", "expected_version": pending["version"]},
        )
        assert decision.status_code == 200
    finally:
      await approver.aclose()
    summary = (await client.get(f"/api/projects/{created['id']}")).json()["data"]
    assert summary["status"] == "completed"
    assert summary["can_start"] is False
    delivered = (await client.get(f"/api/projects/{created['id']}/delivery")).json()["data"]
    await consultant.aclose()
    assert len(delivered["members"]) == 80
    assert delivered["configuration"]["notifications.due_date"] is True
    assert len(delivered["artifacts"]) == 6
    restarted = await client.post(
        f"/api/projects/{created['id']}/runs",
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert restarted.status_code == 409
    assert "不能再次启动" in restarted.json()["detail"]


async def test_approval_rejects_stale_version(client):
    created = await project(client)
    started = await client.post(
        f"/api/projects/{created['id']}/runs",
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    approval = (await client.get(
        f"/api/approvals?run_id={started.json()['data']['id']}"
    )).json()["data"][0]
    approver = await role_client(client, created["id"], "approver", "approver-version@example.com")
    try:
        stale = await approver.post(
            f"/api/approvals/{approval['id']}/decision",
            headers={"Idempotency-Key": str(uuid.uuid4())},
            json={"decision": "approved", "expected_version": approval["version"] + 1},
        )
        assert stale.status_code == 409
        current = (await approver.get(
            f"/api/approvals?run_id={started.json()['data']['id']}"
        )).json()["data"][0]
        assert current["status"] == "pending"
        assert current["version"] == approval["version"]
    finally:
        await approver.aclose()


async def test_active_run_can_be_cancelled_and_retried(client):
    created = await project(client)
    started = await client.post(
        f"/api/projects/{created['id']}/runs",
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    first_run = started.json()["data"]
    cancelled = await client.post(
        f"/api/runs/{first_run['id']}/cancel",
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["status"] == "cancelled"
    assert cancelled.json()["data"]["project_status"] == "blocked"
    approvals = (await client.get(
        f"/api/approvals?run_id={first_run['id']}"
    )).json()["data"]
    assert approvals[0]["status"] == "cancelled"

    retried = await client.post(
        f"/api/projects/{created['id']}/runs",
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert retried.status_code == 200
    assert retried.json()["data"]["run_number"] == 2
    assert retried.json()["data"]["retry_of_run_id"] == first_run["id"]
