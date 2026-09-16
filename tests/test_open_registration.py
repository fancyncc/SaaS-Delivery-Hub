import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from test_api import project_body

from backend.db import SessionLocal
from backend.main import app
from backend.models import (
    AuditEvent,
    CompanyDirectoryEntry,
    EmailVerification,
    MailDelivery,
    Tenant,
    TenantMembership,
    User,
    UserInvitation,
)


def headers(client):
    return {"X-CSRF-Token": client.cookies.get("saas_csrf", ""), "Idempotency-Key": str(uuid4())}


def token(url):
    return parse_qs(urlparse(url).query)["token"][0]


def anonymous():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def register(client, email="personal@example.com"):
    sent = await client.post("/api/auth/register", json={"email": email})
    assert sent.status_code == 200, sent.text
    raw = token(sent.json()["data"]["verification_url"])
    verified = await client.post("/api/auth/verify-email", json={"token": raw, "display_name": "个人用户", "password": "PersonalPass123"})
    assert verified.status_code == 200, verified.text
    return (await client.get("/api/auth/me")).json()["data"]


async def validate(client, csv_text):
    response = await client.post("/api/company/member-imports/validate", headers=headers(client), json={"csv_text": csv_text})
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def commit(client, batch):
    response = await client.post(f"/api/company/member-imports/{batch['id']}/commit", headers=headers(client))
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def test_register_spaces_and_company_creation(client):
    async with anonymous() as person:
        me = await register(person, " Personal@Example.com ")
        assert me["email"] == "personal@example.com" and me["email_verified"]
        assert me["workspace_kind"] == "personal" and len(me["memberships"]) == 1
        personal_id = me["tenant_id"]
        assert (await person.post("/api/auth/register", json={"email": "PERSONAL@example.com"})).status_code == 409
        denied = await person.post("/api/company/invitations", headers=headers(person), json={"email": "any@example.com"})
        assert denied.status_code == 403
        created = await person.post("/api/companies", headers=headers(person), json={"name": "开放公司", "slug": "open-company"})
        assert created.status_code == 200, created.text
        company_id = created.json()["data"]["id"]
        assert (await person.post("/api/companies", headers=headers(person), json={"name": "另一公司", "slug": "another"})).status_code == 409
        assert (await person.get("/api/companies/name-check?name=开放公司")).json()["data"]["similar_name_exists"]
        old_cookie = person.cookies.get("saas_session")
        switched = await person.post(f"/api/auth/spaces/{company_id}/switch", headers=headers(person))
        assert switched.status_code == 200 and person.cookies.get("saas_session") != old_cookie
        me = (await person.get("/api/auth/me")).json()["data"]
        assert me["workspace_kind"] == "company" and len(me["memberships"]) == 2
        assert (await client.post(f"/api/auth/spaces/{personal_id}/switch", headers=headers(client))).status_code == 404
        directory = (await person.get("/api/company/company-directory")).json()["data"]
        assert personal_id not in {r["id"] for r in directory}
        assert (await person.post(f"/api/auth/spaces/{personal_id}/switch", headers=headers(person))).status_code == 200


async def test_legacy_email_verification_required(client):
    # The fixture user already has an enterprise; verification must still be explicit.
    me = (await client.get("/api/auth/me")).json()["data"]
    assert not me["email_verified"]
    assert me["company_creation_requires_verified_email"]
    assert (await client.post("/api/companies", headers=headers(client), json={"name": "公司名称", "slug": "legacy-new"})).status_code == 403
    sent = await client.post("/api/auth/email-verification", headers=headers(client))
    raw = token(sent.json()["data"]["verification_url"])
    async with anonymous() as stranger:
        assert (await stranger.post("/api/auth/verify-email", json={"token": raw})).status_code == 401
    assert (await client.post("/api/auth/verify-email", json={"token": raw})).status_code == 200
    assert (await client.get("/api/auth/me")).json()["data"]["email_verified"]
    assert (await client.post("/api/auth/verify-email", json={"token": raw})).status_code == 410


async def test_expired_and_concurrent_registration():
    async with anonymous() as first, anonymous() as second:
        sent = await first.post("/api/auth/register", json={"email": "race@example.com"})
        raw = token(sent.json()["data"]["verification_url"])
        payload = {"token": raw, "display_name": "并发用户", "password": "RacePassword123"}
        responses = await asyncio.gather(first.post("/api/auth/verify-email", json=payload), second.post("/api/auth/verify-email", json=payload))
        assert sorted(r.status_code for r in responses) == [200, 410]
        sent = await first.post("/api/auth/register", json={"email": "expired@example.com"})
        async with SessionLocal() as session:
            item = await session.scalar(select(EmailVerification).where(EmailVerification.email == "expired@example.com"))
            item.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()
        assert (await second.post("/api/auth/verify-email", json={**payload, "token": token(sent.json()["data"]["verification_url"])})).status_code == 410


async def test_registration_rejects_non_string_email():
    async with anonymous() as client:
        for email in (None, 123, {}, []):
            response = await client.post("/api/auth/register", json={"email": email})
            assert response.status_code == 422


async def test_bulk_duplicates_atomicity_and_activation(client):
    csv_text = "姓名,邮箱,部门,工号\n张三, New@Example.com ,实施部,001\n李四,other@example.com,实施部,002"
    bad = await validate(client, csv_text + "\n王五,NEW@example.com,实施部,002")
    assert not bad["valid"] and len(bad["rows"][-1]["errors"]) == 2
    assert (await client.post(f"/api/company/member-imports/{bad['id']}/commit", headers=headers(client))).status_code == 409
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(UserInvitation)) == 0
    batch = await validate(client, csv_text)
    assert batch["rows"][0]["employee_number"] == "001"
    result = await commit(client, batch)
    assert len(result["invitations"]) == 2
    again = await commit(client, batch)
    assert len(again["invitations"]) == 2
    duplicate = await validate(client, csv_text)
    assert duplicate["valid"] and duplicate["skip_count"] == 2
    changed = await validate(client, csv_text.replace("张三", "其他姓名"))
    assert not changed["valid"]
    number_conflict = await validate(client, "姓名,邮箱,部门,工号\n王五,third@example.com,实施部,001")
    assert not number_conflict["valid"]
    async with anonymous() as member:
        raw = token(result["invitations"][0]["invitation_url"])
        response = await member.post(f"/api/auth/invitations/{raw}/accept", json={"display_name": "张三", "password": "MemberPassword123"})
        assert response.status_code == 200, response.text
        me = (await member.get("/api/auth/me")).json()["data"]
        assert me["workspace_kind"] == "company" and len(me["memberships"]) == 2 and me["email_verified"]
        assert me["company_role_code"] == "company_member"
        assert (await member.post(f"/api/auth/invitations/{raw}/accept", json={})).status_code == 410
        assert (await member.get(f"/api/company/member-imports/{batch['id']}")).status_code == 403
    assert (await validate(client, csv_text))["skip_count"] == 2


async def test_existing_account_join_does_not_replace_credentials(client):
    async with anonymous() as person, anonymous() as stranger:
        me = await register(person)
        batch = await validate(client, "姓名,邮箱,部门,工号\n公司内称呼,personal@example.com,实施部,009")
        result = await commit(client, batch)
        raw = token(result["invitations"][0]["invitation_url"])
        async with SessionLocal() as session:
            original_hash = (await session.get(User, me["id"])).password_hash
        attempt = {"display_name": "被覆盖姓名", "password": "OtherPassword123"}
        assert (await stranger.post(f"/api/auth/invitations/{raw}/accept", json=attempt)).status_code == 401
        response = await person.post(f"/api/auth/invitations/{raw}/accept", headers=headers(person), json=attempt)
        assert response.status_code == 200, response.text
        async with SessionLocal() as session:
            user = await session.get(User, me["id"])
            assert user.password_hash == original_hash and user.display_name == "个人用户"
        assert (await stranger.post("/api/auth/login", json={"email": me["email"], "password": "PersonalPass123"})).status_code == 200
        assert (await stranger.get("/api/auth/me")).json()["data"]["workspace_kind"] == "company"


async def test_commit_rechecks_and_resend_invalidates_link(client):
    csv_text = "姓名,邮箱,部门,工号\n成员姓名,recheck@example.com,实施部,01"
    batch1 = await validate(client, csv_text)
    batch2 = await validate(client, csv_text)
    first = await commit(client, batch1)
    second = await commit(client, batch2)
    assert second["new_count"] == 0 and second["skip_count"] == 1
    item = first["invitations"][0]
    resent = await client.post(f"/api/company/invitations/{item['id']}/resend", headers=headers(client))
    assert resent.status_code == 200, resent.text
    async with anonymous() as person:
        raw = token(item["invitation_url"])
        assert (await person.post(f"/api/auth/invitations/{raw}/accept", json={"display_name": "成员姓名", "password": "TestPassword123"})).status_code == 410
    revoked = await client.post(f"/api/company/invitations/{resent.json()['data']['id']}/revoke", headers=headers(client))
    assert revoked.status_code == 200


async def test_one_enterprise_database_constraint(client):
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.email == "company-admin@example.com"))
        tenant = Tenant(name="其他公司", slug="constraint-company")
        session.add(tenant)
        await session.flush()
        session.add(TenantMembership(tenant_id=tenant.id, user_id=user.id, role="tenant_member"))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_personal_complete_workflow_and_company_isolation(client):
    async with anonymous() as person:
        me = await register(person)
        body = {**project_body(), "employee_count": 1, "migration_scope": "导入 1 名成员"}
        created = await person.post("/api/projects", headers=headers(person), json=body)
        assert created.status_code == 200, created.text
        project_id = created.json()["data"]["id"]
        company_id = (await client.get("/api/auth/me")).json()["data"]["tenant_id"]
        forbidden = await person.post(f"/api/projects/{project_id}/collaborating-companies", headers=headers(person), json={"tenant_id": company_id})
        assert forbidden.status_code == 403
        run_response = await person.post(f"/api/projects/{project_id}/runs", headers=headers(person))
        assert run_response.status_code == 200, run_response.text
        run_id = run_response.json()["data"]["id"]
        for _ in range(4):
            approvals = (await person.get(f"/api/approvals?run_id={run_id}")).json()["data"]
            pending = next(a for a in approvals if a["status"] == "pending")
            decision = await person.post(f"/api/approvals/{pending['id']}/decision", headers=headers(person), json={"decision": "approved", "expected_version": pending["version"]})
            assert decision.status_code == 200, decision.text
            if decision.json()["data"]["run_status"] == "preparing_materials":
                uploaded = await person.post("/api/imports/validate", headers=headers(person), json={"project_id": project_id, "run_id": run_id,
                    "csv_text": "name,email,department,role\n测试,test@example.com,设计部,admin"})
                assert uploaded.status_code == 200 and uploaded.json()["data"]["valid"], uploaded.text
        assert (await person.get(f"/api/runs/{run_id}")).json()["data"]["status"] == "succeeded"
        async with SessionLocal() as session:
            events = list((await session.scalars(select(AuditEvent).where(AuditEvent.tenant_id == me["tenant_id"], AuditEvent.event_type == "approval.approved"))).all())
            assert len(events) == 4 and all(e.payload["personal_confirmation"] for e in events)
        assert (await client.get(f"/api/projects/{project_id}")).status_code == 404


async def test_bulk_mail_is_transactional_and_replay_safe(client, monkeypatch):
    from backend.config import get_settings
    monkeypatch.setattr(get_settings(), "mail_debug", False)
    batch = await validate(client, "姓名,邮箱,部门,工号\n邮件用户,mail-member@example.com,实施部,008")
    await commit(client, batch)
    await commit(client, batch)
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(MailDelivery)) == 1
        assert await session.scalar(select(func.count()).select_from(CompanyDirectoryEntry)) == 1


async def test_company_names_slugs_and_recheck_after_join(client):
    async with anonymous() as first, anonymous() as second:
        await register(first, "first-company@example.com")
        await register(second, "second-company@example.com")
        batch = await validate(client, "姓名,邮箱,部门,工号\n个人用户,second-company@example.com,实施部,001")
        assert batch["valid"]
        one = await first.post("/api/companies", headers=headers(first), json={"name": "同名公司", "slug": "same-name"})
        assert one.status_code == 200
        collision = await second.post("/api/companies", headers=headers(second), json={"name": "同名公司", "slug": "same-name"})
        assert collision.status_code == 409
        two = await second.post("/api/companies", headers=headers(second), json={"name": "同名公司", "slug": "different-slug"})
        assert two.status_code == 200
        changed = await client.post(f"/api/company/member-imports/{batch['id']}/commit", headers=headers(client))
        assert changed.status_code == 409 and "同名公司" not in changed.text


async def test_concurrent_activation_consumes_invitation_once(client):
    batch = await validate(client, "姓名,邮箱,部门,工号\n并发成员,concurrent-member@example.com,实施部,001")
    result = await commit(client, batch)
    raw = token(result["invitations"][0]["invitation_url"])
    async with anonymous() as first, anonymous() as second:
        payload = {"display_name": "并发成员", "password": "ConcurrentPass123"}
        responses = await asyncio.gather(first.post(f"/api/auth/invitations/{raw}/accept", json=payload),
                                         second.post(f"/api/auth/invitations/{raw}/accept", json=payload))
        assert sorted(response.status_code for response in responses) == [200, 410]
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.email == "concurrent-member@example.com"))
        assert await session.scalar(select(func.count()).select_from(TenantMembership).where(
            TenantMembership.user_id == user.id, TenantMembership.workspace_kind == "company")) == 1


@pytest.mark.parametrize("content", [
    "姓名,邮箱\n姓名,a@example.com",
    "姓名,name,邮箱,部门\n姓名,姓名,a@example.com,部门",
    "姓名,邮箱,部门\n姓名,a@example.com,部门,多余列",
    "姓名,邮箱,部门\n" + "姓名,a@example.com,部门\n" * 1001,
], ids=["missing-column", "duplicate-column", "extra-cell", "too-many-rows"])
async def test_invalid_bulk_file_shapes_are_rejected(client, content):
    response = await client.post("/api/company/member-imports/validate", headers=headers(client), json={"csv_text": content})
    assert response.status_code == 422
