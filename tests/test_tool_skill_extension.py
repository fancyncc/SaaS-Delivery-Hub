import json
import uuid

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from test_api import project, project_body, role_client

from backend import skill_runtime
from backend.agent_loop import default_plan, validate_plan
from backend.agent_types import Criterion, Milestone
from backend.config import get_settings
from backend.db import SessionLocal
from backend.models import AgentRun, ImportJob, Project, SaaSMember, SaaSWorkspace
from backend.run_snapshots import RunSnapshot, inputs
from backend.tool_registry import available, execute
from backend.tool_types import ReadOfficialArguments, SearchOfficialArguments
from backend.tools import official_web
from backend.tools.materials import check_brief, preview


@pytest.mark.parametrize("name", ["甲方实施项目", "乙方实施项目", "丙方实施项目", "丁方实施项目", "戊方实施项目", "己方实施项目"])
def test_complete_brief_preserves_facts(name):
    draft = {**project_body(), "name": name, "departments": ["设计部", "设计部", "市场部"]}
    result = check_brief(draft)
    assert result["complete"] and result["submission"]["employee_count"] == 80
    assert result["submission"]["departments"] == ["设计部", "市场部"]
    assert draft["departments"] == ["设计部", "设计部", "市场部"]


@pytest.mark.parametrize("field", ["name", "customer_name", "customer_contact", "contact_email", "employee_count", "target_go_live_date"])
def test_missing_brief_does_not_invent_typed_values(field):
    draft = project_body()
    draft[field] = "待补充"
    result = check_brief(draft)
    assert field in result["missing_fields"] and result["submission"] is None
    assert not result["complete"] and result["draft"][field] == "待补充"


@pytest.mark.parametrize("field,value", [("contact_email", "bad"), ("employee_count", 0),
    ("target_go_live_date", "2026-99-99"), ("requirements_text", "权限"), ("departments", []),
    ("departments", [" "])])
def test_invalid_brief_returns_actionable_check(field, value):
    result = check_brief({**project_body(), field: value})
    assert not result["complete"] and result["issues"] and result["submission"] is None


def test_skills_are_discovered_bounded_and_reject_escape(tmp_path, monkeypatch):
    items = skill_runtime.catalog()
    assert len(items) == 4
    assert items["create-implementation-project-document"]["resources"]
    assert skill_runtime.choose(items, "collect_requirements")["name"] == "create-implementation-project-document"
    monkeypatch.setattr(get_settings(), "skill_max_tokens", 100)
    with pytest.raises(HTTPException):
        skill_runtime.choose(items, "collect_requirements")
    directory = tmp_path / "bad-skill"
    directory.mkdir()
    (directory / "SKILL.md").write_text("---\nname: bad-skill\ndescription: test\n---\nreferences/../../escape.md", encoding="utf-8")
    with pytest.raises(ValueError):
        skill_runtime.catalog(tmp_path)


async def test_skill_contents_are_frozen_for_queued_run(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "skill_runtime_enabled", True)
    monkeypatch.setattr(get_settings(), "execution_mode", "worker")
    frozen = skill_runtime.catalog()

    p = await project(client)
    started = await client.post(f"/api/projects/{p['id']}/runs", headers={"Idempotency-Key": uuid.uuid4().hex})
    assert started.status_code == 200, started.text
    async with SessionLocal() as session:
        run = await session.get(AgentRun, started.json()["data"]["id"])
        snapshot = await inputs(session, run)
        assert snapshot.payload["skills"] == frozen
        monkeypatch.setattr(skill_runtime, "catalog", lambda: {})
        method = await skill_runtime.activate(session, run, "collect_requirements")
        assert method["content"] == frozen[method["name"]]["content"]
    monkeypatch.setattr(skill_runtime, "catalog", lambda: frozen)


async def test_enabled_skills_record_usage_without_bypassing_approval(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "skill_runtime_enabled", True)

    p = await project(client)
    started = await client.post(f"/api/projects/{p['id']}/runs", headers={"Idempotency-Key": uuid.uuid4().hex})
    assert started.status_code == 200, started.text
    assert started.json()["data"]["status"] == "waiting_approval"
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(RunSnapshot).where(
            RunSnapshot.run_id == started.json()["data"]["id"], RunSnapshot.kind == "skill")) >= 3


async def test_draft_api_and_preview_never_initialize_workspaces(client):
    result = await client.post("/api/project-briefs/check", json={"draft": {"employee_count": None}})
    assert result.status_code == 200 and not result.json()["data"]["complete"]
    p = await project(client)
    csv = "name,email,department,role\n甲,a@example.com,设计部,member\n"
    response = await client.post(f"/api/projects/{p['id']}/imports/preview", json={"csv_text": csv})
    assert response.status_code == 403
    consultant = await role_client(client, p["id"], "implementation_consultant", "preview@example.com")
    try:
        response = await consultant.post(f"/api/projects/{p['id']}/imports/preview", json={"csv_text": csv})
        assert response.status_code == 200 and response.json()["data"]["target_status"] == "unavailable"
    finally:
        await consultant.aclose()
    async with SessionLocal() as session:
        for model in (SaaSWorkspace, SaaSMember, ImportJob):
            assert await session.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.parametrize("row,valid", [("甲,a@example.com,设计部,member", True),
    ("甲,a@example.com,错误部门,member", False), ("甲,a@example.com,设计部,unknown", False),
    ("甲,bad,设计部,member", False)])
async def test_preview_matches_target_rules_without_writes(client, row, valid):
    p = await project(client)
    async with SessionLocal() as session:
        proj = await session.get(Project, p["id"])
        from backend.delivery import workspace
        await workspace(session, proj)
        await session.commit()
        result = await preview(session, proj, "name,email,department,role\n" + row + "\n")
        assert result["valid"] == valid and not result["can_execute"]
        assert not session.new and not session.dirty
        assert await session.scalar(select(func.count()).select_from(SaaSMember)) == 0
        assert await session.scalar(select(func.count()).select_from(ImportJob)) == 0


def test_optional_stage_contract_and_unknown_parameters(monkeypatch):
    assert not available()
    monkeypatch.setattr(get_settings(), "skill_runtime_enabled", True)
    plan = default_plan("discovery")
    optional = Milestone(id="check", title="检查", tool="check_project_brief", criteria=[Criterion(tool="check_project_brief")])
    plan.milestones.insert(0, optional)
    plan.milestones[1].dependencies = ["check"]
    validate_plan(plan, "discovery")
    optional.tool = "preview_import"
    with pytest.raises(ValueError):
        validate_plan(plan, "discovery")
    with pytest.raises(ValueError):
        SearchOfficialArguments(product="demo", topic="permissions", tenant_id="wrong")


@pytest.fixture
def web_settings(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "official_web_enabled", True)
    monkeypatch.setattr(s, "official_search_api_key", "fixture-key")
    monkeypatch.setattr(s, "official_products", {"demo": {"name": "Public Demo", "domains": ["docs.example.com"],
        "topics": {"permissions": "role permissions"}, "versions": ["v1"]}})
    return s


@pytest.mark.parametrize("url", ["http://docs.example.com/", "https://docs.example.com:444/",
    "https://docs.example.com.evil.test/", "https://user@docs.example.com/", "https://127.0.0.1/",
    "https://docs.example.com:bad/"])
def test_official_url_policy_denies_unsafe_destinations(url):
    with pytest.raises(HTTPException):
        official_web.checked_url(url, ["docs.example.com", "127.0.0.1"])


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:8.8.8.8", "224.0.0.1"])
async def test_dns_rejects_private_and_special_addresses(address, monkeypatch):
    async def resolve(*args, **kwargs):
        return [(None, None, None, None, (address, 443))]
    monkeypatch.setattr(official_web.asyncio.get_running_loop(), "getaddrinfo", resolve)
    with pytest.raises(HTTPException):
        await official_web.public_addresses("docs.example.com")


async def test_public_search_query_scope_and_page_evidence(web_settings, monkeypatch):
    calls = []
    async def fetch(url, domains, **kwargs):
        calls.append(url)
        if "api.search.brave.com" in url:
            return json.dumps({"web": {"results": [{"title": "权限", "url": "https://docs.example.com/roles"},
                {"url": "https://evil.test/"}]}}).encode(), url, "application/json"
        return b"<html><script>steal</script><p>Public permission guide</p></html>", url, "text/html"
    monkeypatch.setattr(official_web, "fetch_bytes", fetch)
    found = await official_web.search(SearchOfficialArguments(product="demo", topic="permissions", version="v1"), "scope-a")
    assert len(found["results"]) == 1 and "customer" not in calls[0]
    args = ReadOfficialArguments(reference=found["results"][0]["reference"])
    with pytest.raises(HTTPException):
        await official_web.read(args, "scope-b")
    result = await official_web.read(args, "scope-a")
    source = result["sources"][0]
    assert "steal" not in source["text"] and "Public permission guide" in source["text"]
    assert source["retrieved_at"] and source["content_hash"] and source["published_at"] is None
    web_settings.official_products["demo"]["domains"] = ["new.example.com"]
    with pytest.raises(HTTPException):
        await official_web.read(args, "scope-a")


@pytest.mark.parametrize("condition", ["no_results", "unsupported_version", "missing_product", "missing_provider"])
async def test_official_evidence_unavailable_is_explicit(condition, web_settings, monkeypatch):
    async def empty(*args, **kwargs):
        return b'{"web":{"results":[]}}', "https://api.search.brave.com", "application/json"
    monkeypatch.setattr(official_web, "fetch_bytes", empty)
    if condition == "missing_provider":
        web_settings.official_search_api_key = ""
    args = SearchOfficialArguments(product="missing" if condition == "missing_product" else "demo",
                                   topic="permissions", version="v2" if condition == "unsupported_version" else "")
    if condition == "no_results":
        assert (await official_web.search(args, "scope"))["status"] == "no_results"
    else:
        with pytest.raises(HTTPException):
            await official_web.search(args, "scope")


async def test_transient_web_errors_return_limitations(web_settings, monkeypatch):
    async def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("provider unavailable")
    monkeypatch.setattr(official_web, "search", timeout)
    result = await execute(None, None, "search_official_docs", {"product": "demo", "topic": "permissions"}, scope="scope")
    assert result["result"]["status"] == "unavailable" and not result["sources"]


async def test_mcp_optional_tools_preserve_ownership_and_budget(client, web_settings, monkeypatch):
    created = await client.post("/api/chat/conversations", json={"title": "测试"})
    assert created.status_code == 200, created.text
    chat = created.json()["data"]
    async def empty(*args, **kwargs):
        return {"status": "no_results", "results": []}
    monkeypatch.setattr(official_web, "search", empty)
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search_official_docs",
        "arguments": {"conversation_id": chat["id"], "product": "demo", "topic": "permissions"}}}
    first = (await client.post("/api/mcp", json=body)).json()
    assert not first["result"]["isError"], first
    second = (await client.post("/api/mcp", json=body)).json()
    assert second["result"]["isError"]
    detail = (await client.get(f"/api/chat/conversations/{chat['id']}")).json()["data"]
    assert not detail["documents"] and not detail["messages"]
