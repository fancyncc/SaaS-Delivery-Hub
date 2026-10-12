from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from backend.main import app


def csrf(client: AsyncClient) -> dict[str, str]:
    return {"Idempotency-Key": str(uuid4()), "X-CSRF-Token": client.cookies.get("saas_csrf") or ""}


def invitation_token(url: str) -> str:
    return parse_qs(urlparse(url).query)["token"][0]


def project_body(name: str) -> dict:
    return {
        "name": name,
        "customer_name": "权限测试客户",
        "customer_contact": "客户联系人",
        "contact_email": "contact@example.com",
        "employee_count": 20,
        "target_go_live_date": "2030-01-01",
        "departments": ["项目部"],
        "requirements_text": "这是用于验证数据库角色、临时授权和支持访问隔离的完整实施需求。",
    }


async def test_primary_role_compatibility_and_temporary_capability(client):
    project = (await client.post("/api/projects", headers=csrf(client), json=project_body("临时授权项目"))).json()["data"]
    tenant_id = (await client.get("/api/auth/me")).json()["data"]["tenant_id"]
    invited = await client.post(
        "/api/company/invitations", headers=csrf(client),
        json={"tenant_id": tenant_id, "email": "capability@example.com", "company_role": "tenant_member"},
    )
    member = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        await member.post(
            f"/api/auth/invitations/{invitation_token(invited.json()['data']['invitation_url'])}/accept",
            json={"display_name": "临时成员", "password": "Capability123"},
        )
        member.headers["X-CSRF-Token"] = member.cookies.get("saas_csrf")
        member_id = (await member.get("/api/auth/me")).json()["data"]["id"]
        conflict = await client.post(
            f"/api/projects/{project['id']}/members", headers=csrf(client),
            json={"user_id": member_id, "primary_role_code": "viewer", "project_role": "approver"},
        )
        assert conflict.status_code == 422
        assigned = await client.post(
            f"/api/projects/{project['id']}/members", headers=csrf(client),
            json={"user_id": member_id, "project_role": "viewer"},
        )
        assert assigned.status_code == 200
        assert assigned.json()["data"]["primary_role_code"] == "viewer"
        membership_id = assigned.json()["data"]["id"]
        assert (await member.post(f"/api/projects/{project['id']}/runs", headers=csrf(member))).status_code == 403
        forbidden = await client.post(
            f"/api/projects/{project['id']}/members/{membership_id}/capability-grants",
            headers=csrf(client),
            json={"permission_code": "approval.decide", "reason": "不允许的权限",
                  "expires_at": (datetime.now(UTC) + timedelta(hours=2)).isoformat()},
        )
        assert forbidden.status_code == 422
        granted = await client.post(
            f"/api/projects/{project['id']}/members/{membership_id}/capability-grants",
            headers=csrf(client),
            json={"permission_code": "run.start", "reason": "临时协助执行测试",
                  "expires_at": (datetime.now(UTC) + timedelta(hours=2)).isoformat()},
        )
        assert granted.status_code == 200
        assert (await member.post(f"/api/projects/{project['id']}/runs", headers=csrf(member))).status_code == 200
    finally:
        await member.aclose()


async def test_support_access_requires_customer_admin_and_is_read_only(client, platform_client):
    created = await platform_client.post(
        "/api/platform/tenants", headers=csrf(platform_client),
        json={"name": "支持目标公司", "slug": "support-target", "admin_email": "support-admin@example.com",
              "admin_name": "目标管理员"},
    )
    company = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        accepted = await company.post(
            f"/api/auth/invitations/{invitation_token(created.json()['data']['invitation_url'])}/accept",
            json={"display_name": "目标管理员", "password": "TargetAdmin123"},
        )
        assert accepted.status_code == 200, accepted.text
        company.headers["X-CSRF-Token"] = company.cookies.get("saas_csrf")
        project = (await company.post(
            "/api/projects", headers=csrf(company), json=project_body("客户支持项目")
        )).json()["data"]
        assert (await client.get(f"/api/projects/{project['id']}")).status_code == 404
        requested = await platform_client.post(
            "/api/platform/support-access-grants", headers=csrf(platform_client),
            json={
                "target_tenant_id": created.json()["data"]["id"],
                "target_project_id": project["id"],
                "reason": "排查客户报告的只读执行详情问题",
                "permission_codes": ["project.view", "run.view"],
            },
        )
        assert requested.status_code == 200
        grant_id = requested.json()["data"]["id"]
        assert (await platform_client.get(f"/api/projects/{project['id']}")).status_code == 404
        approved = await company.post(
            f"/api/company/support-access-grants/{grant_id}/approve",
            headers=csrf(company), json={"comment": "同意只读排查"},
        )
        assert approved.status_code == 200
        assert (await platform_client.get(f"/api/projects/{project['id']}")).status_code == 200
        assert (await platform_client.post(f"/api/projects/{project['id']}/runs", headers=csrf(platform_client))).status_code == 403
        assert (await company.post(
            f"/api/company/support-access-grants/{grant_id}/revoke", headers=csrf(company)
        )).status_code == 200
        assert (await platform_client.get(f"/api/projects/{project['id']}")).status_code == 404
    finally:
        await company.aclose()
