import asyncio
import json
import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select, update
from test_api import project
from test_delivery import preparing
from test_tool_skill_extension import web_settings as _web_settings

from backend import chat_tools, method_reports, skill_runtime
from backend.chat_mcp import call_tool
from backend.chat_models import ChatConversation
from backend.config import get_settings
from backend.db import SessionLocal
from backend.models import AgentRun, ProjectArtifact, TenantMembership
from backend.tool_types import SearchOfficialArguments
from backend.tools import official_web
from backend.worker import actor_context

web_settings = _web_settings


@pytest.mark.parametrize("case", ["pinned", "redirect", "private_redirect", "size", "type", "redirect_limit"])
async def test_fetch_pins_dns_and_rechecks_redirects(case, web_settings, monkeypatch):
    calls = []
    async def dns(host):
        assert host == "docs.example.com"
        return ["8.8.8.8"]
    monkeypatch.setattr(official_web, "public_addresses", dns)
    def handler(request):
        calls.append(request)
        assert request.url.host == "8.8.8.8"
        assert request.headers["host"] == "docs.example.com"
        assert request.extensions["sni_hostname"] == "docs.example.com"
        if case == "redirect_limit" or case == "redirect" and len(calls) == 1:
            return httpx.Response(302, headers={"location": "/guide"})
        if case == "private_redirect":
            return httpx.Response(302, headers={"location": "https://127.0.0.1/secrets"})
        return httpx.Response(200, content=b"official guide", headers={
            "content-type": "application/pdf" if case == "type" else "text/html"})
    factory = httpx.AsyncClient
    monkeypatch.setattr(official_web.httpx, "AsyncClient", lambda **kw: factory(transport=httpx.MockTransport(handler), **kw))
    if case == "size":
        monkeypatch.setattr(web_settings, "official_web_max_bytes", 3)
    if case in {"private_redirect", "size", "type", "redirect_limit"}:
        with pytest.raises(HTTPException):
            await official_web.fetch_bytes("https://docs.example.com/start", ["docs.example.com"])
        if case == "private_redirect":
            assert len(calls) == 1
    else:
        body, url, _ = await official_web.fetch_bytes("https://docs.example.com/start", ["docs.example.com"])
        assert body == b"official guide"
        assert url == "https://docs.example.com/" + ("guide" if case == "redirect" else "start")


async def test_fetch_timeout_includes_dns(web_settings, monkeypatch):
    async def slow(host):
        await asyncio.sleep(0.1)
        return ["8.8.8.8"]
    monkeypatch.setattr(official_web, "public_addresses", slow)
    monkeypatch.setattr(web_settings, "official_web_timeout", 0.01)
    with pytest.raises(TimeoutError):
        await official_web.fetch_bytes("https://docs.example.com/", ["docs.example.com"])


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"web":null}', b'{"web":{"results":{}}}'])
async def test_malformed_search_results_are_explicit(body, web_settings, monkeypatch):
    async def fetch(*args, **kwargs):
        return body, "https://api.search.brave.com/", "application/json"
    monkeypatch.setattr(official_web, "fetch_bytes", fetch)
    with pytest.raises(HTTPException) as error:
        await official_web.search(SearchOfficialArguments(product="demo", topic="permissions"), "scope")
    assert error.value.status_code == 503


@pytest.mark.parametrize("body", [b'<input type="password">Sign in', b'<script>loadPage()</script>'])
async def test_login_and_dynamic_pages_cannot_be_fact_sources(body, web_settings, monkeypatch):
    async def fetch(url, *args, **kwargs):
        if "api.search.brave.com" in url:
            return b'{"web":{"results":[{"url":"https://docs.example.com/"}]}}', url, "application/json"
        return body, url, "text/html"
    monkeypatch.setattr(official_web, "fetch_bytes", fetch)
    found = await official_web.search(SearchOfficialArguments(product="demo", topic="permissions"), "scope")
    from backend.tool_types import ReadOfficialArguments
    result = await official_web.read(ReadOfficialArguments(reference=found["results"][0]["reference"]), "scope")
    assert result["status"] == "unavailable" and not result["sources"]


@pytest.mark.parametrize("change", ["version", "archived", "membership", "session"])
async def test_mcp_reauthorizes_after_network(client, web_settings, monkeypatch, change):
    chat = (await client.post("/api/chat/conversations", json={})).json()["data"]
    async with SessionLocal() as session:
        row = await session.get(ChatConversation, chat["id"])
        user = await actor_context(session, SimpleNamespace(actor_id=row.user_id, tenant_id=row.tenant_id))
        if change == "session":
            from dataclasses import replace

            from backend.models import AuthSession
            auth = await session.scalar(select(AuthSession).where(AuthSession.user_id == row.user_id))
            user = replace(user, session_id=auth.id)
        async def search(*args):
            async with SessionLocal() as other:
                if change == "membership":
                    await other.execute(update(TenantMembership).where(TenantMembership.user_id == row.user_id).values(status="inactive"))
                elif change == "session":
                    from datetime import UTC, datetime

                    from backend.models import AuthSession
                    await other.execute(update(AuthSession).where(AuthSession.id == user.session_id).values(revoked_at=datetime.now(UTC)))
                else:
                    values = {"version": row.version + 1} if change == "version" else {"archived": True}
                    await other.execute(update(ChatConversation).where(ChatConversation.id == row.id).values(**values))
                await other.commit()
            return {"status": "no_results", "results": []}
        monkeypatch.setattr(official_web, "search", search)
        with pytest.raises(HTTPException) as error:
            await call_tool(session, user, "search_official_docs", {
                "conversation_id": row.id, "product": "demo", "topic": "permissions"}, {})
        assert error.value.status_code in {401, 403, 409}
        await session.rollback()
    async with SessionLocal() as session:
        row = await session.get(ChatConversation, chat["id"])
        assert row.tool_state["counts"]["search_official_docs"] == 1
        assert not row.tool_state["calls"] and not row.documents


async def test_native_official_search_read_protocol_and_private_evidence(client, web_settings, monkeypatch):
    chat = (await client.post("/api/chat/conversations", json={})).json()["data"]
    for key, value in {"model_mode": "real", "model_api_style": "chat_completions", "model_base_url": "https://model.test/v1",
                       "model_name": "fixture", "model_api_key": "fixture", "llm_mode": "online"}.items():
        monkeypatch.setattr(get_settings(), key, value)
    requests = []
    async def fetch(url, *args, **kwargs):
        if "api.search.brave.com" in url:
            return b'{"web":{"results":[{"title":"Roles","url":"https://docs.example.com/roles"}]}}', url, "application/json"
        return b"Official product roles guide", url, "text/plain"
    monkeypatch.setattr(official_web, "fetch_bytes", fetch)
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        number = len(requests)
        if number == 1:
            name, args = "search_authorized_context", {"query": "官方权限文档"}
        elif number == 2:
            name, args = "search_official_docs", {"product": "demo", "topic": "permissions"}
        elif number == 3:
            found = json.loads(payload["messages"][-1]["content"])
            name, args = "read_official_page", {"reference": found["result"]["results"][0]["reference"]}
        else:
            return httpx.Response(200, json={"choices": [{"message": {"content": "stop"}}]})
        return httpx.Response(200, json={"choices": [{"message": {"tool_calls": [{"id": f"call-{number}",
            "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}}]})
    factory = httpx.AsyncClient
    monkeypatch.setattr(chat_tools.httpx, "AsyncClient", lambda **kw: factory(transport=httpx.MockTransport(handler), **kw))
    async with SessionLocal() as session:
        row = await session.get(ChatConversation, chat["id"])
        user = await actor_context(session, SimpleNamespace(actor_id=row.user_id, tenant_id=row.tenant_id))
        observations = [item async for item in chat_tools.tool_context(session, row, user, "请核实最新官方产品权限", {})]
        assert len(requests) == 4 and len(observations) == 3
        assert observations[0]["tool"] == "search_authorized_context"
        official = [s for s in observations[-1]["sources"] if s.get("kind") == "official_web"]
        assert len(official) == 1 and official[0]["content_hash"]
        assert row.tool_state["counts"] == {"search_official_docs": 1, "read_official_page": 1}
        assert not row.documents and not row.messages


async def test_skill_reports_follow_real_failed_acceptance(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "skill_runtime_enabled", True)

    p, run_id, approver = await preparing(client)
    try:
        uploaded = await client.post("/api/imports/validate", headers={"Idempotency-Key": uuid.uuid4().hex}, json={
            "project_id": p["id"], "run_id": run_id, "csv_text": "name,email,department,role\nA,a@example.com,设计部,admin"})
        assert uploaded.status_code == 200 and uploaded.json()["data"]["valid"]
        pending = next(a for a in (await approver.get(f"/api/approvals?run_id={run_id}")).json()["data"] if a["status"] == "pending")
        response = await approver.post(f"/api/approvals/{pending['id']}/decision", headers={"Idempotency-Key": uuid.uuid4().hex},
            json={"decision": "approved", "expected_version": pending["version"]})
        assert response.status_code == 200, response.text
        async with SessionLocal() as session:
            run = await session.get(AgentRun, run_id)
            assert not run.state["acceptance_report"]["ready"]
            artifacts = {a.kind: a for a in await session.scalars(select(ProjectArtifact).where(ProjectArtifact.run_id == run_id))}
            assert {"configuration_review", "migration_preparation", "training_preparation", "acceptance_notes"} <= set(artifacts)
            notes = json.loads(artifacts["acceptance_notes"].content)
            assert notes["advisory"] and not notes["evidence"]["actual_checks"]["ready"]
            assert "整改" in notes["report"]["sections"][0]["guidance"]
    finally:
        await approver.aclose()


async def test_method_report_rejects_fabricated_citations(monkeypatch):
    token = skill_runtime.selected_skill.set(skill_runtime.catalog()["review-configuration-proposal"])
    monkeypatch.setattr(get_settings(), "model_mode", "real")
    async def generate(*args):
        return method_reports.MethodReport(sections=[{"title": "review", "guidance": "claims", "evidence_ids": ["invented"]}], questions=[])
    monkeypatch.setattr("backend.intelligence.structured", generate)
    try:
        with pytest.raises(HTTPException) as error:
            await method_reports.write(None, None, None, "review", "Review", {"real": {}}, [
                {"title": "review", "guidance": "based on input", "evidence_ids": ["real"]}])
        assert error.value.status_code == 422
    finally:
        skill_runtime.selected_skill.reset(token)


async def test_agent_optional_budget_replay_and_frozen_web_evidence(client, web_settings, monkeypatch):
    from backend.agent_budget import BudgetExceeded
    from backend.agent_loop import context_for, execute_action
    from backend.agent_types import Criterion, Milestone, ToolAction
    from backend.run_snapshots import execution_scope
    from backend.schemas import ImplementationGraphState
    from backend.tool_registry import official_evidence

    monkeypatch.setattr(get_settings(), "execution_mode", "worker")

    p = await project(client)
    started = (await client.post(f"/api/projects/{p['id']}/runs", headers={"Idempotency-Key": uuid.uuid4().hex})).json()["data"]
    calls = []
    async def fetch(url, *args, **kwargs):
        calls.append(url)
        if "api.search.brave.com" in url:
            return b'{"web":{"results":[{"url":"https://docs.example.com/roles"}]}}', url, "application/json"
        return b"Official roles evidence", url, "text/plain"
    monkeypatch.setattr(official_web, "fetch_bytes", fetch)
    async with SessionLocal() as session:
        run = await session.get(AgentRun, started["id"])
        state = ImplementationGraphState.model_validate(run.state)
        state.agent.stage, state.agent.plan_version, state.agent.rounds = "discovery", 1, 1
        milestone = Milestone(id="official-search", title="search", tool="search_official_docs", criteria=[Criterion(tool="search_official_docs")])
        action = ToolAction(tool="search_official_docs", arguments={"product": "demo", "topic": "permissions"})
        async with execution_scope(session, run):
            found = await execute_action(session, run, state, milestone, action)
            assert found.status == "succeeded"
            again = await execute_action(session, run, state, milestone, action)
            assert again.action_id == found.action_id and len(calls) == 1
            state.agent.rounds += 1
            with pytest.raises(BudgetExceeded):
                await execute_action(session, run, state, milestone, action)
            assert state.agent.tool_counts == {"discovery:search_official_docs": 1}
            read = Milestone(id="official-read", title="read", tool="read_official_page", criteria=[Criterion(tool="read_official_page")])
            observation = await execute_action(session, run, state, read, ToolAction(tool="read_official_page", arguments={
                "reference": found.result["result"]["results"][0]["reference"]}))
            assert observation.status == "succeeded"
            frozen = await official_evidence(session, run)
            assert len(frozen) == 1 and frozen[0]["text"] == "Official roles evidence"
            context = await context_for(session, run, state, "collect_requirements")
            assert frozen[0]["id"] in {source["id"] for source in context["knowledge"]}
            run.state = state.model_dump(mode="json")
            await session.commit()
    async with SessionLocal() as session:
        run = await session.get(AgentRun, started["id"])
        restored = ImplementationGraphState.model_validate(run.state)
        assert restored.agent.tool_counts["discovery:search_official_docs"] == 1
        assert await official_evidence(session, run) == frozen


@pytest.mark.parametrize("text", ["员工人数：79人", "成员共81名", "员工规模为120人", "成员数量=2人"])
def test_explicit_headcount_conflicts_require_clarification(text):
    from test_api import project_body

    from backend.tools.materials import check_brief
    result = check_brief({**project_body(), "requirements_text": text + "；需要配置部门权限并组织培训。"})
    assert not result["complete"] and result["submission"] is None
    assert any(issue["code"] == "fact_conflict" for issue in result["issues"])


@pytest.mark.parametrize("frontmatter", ["name: 42\ndescription: methods", "name: review\ndescription: []",
    "name: review\ndescription: methods\nversion: '1'", "name: review\ndescription: methods\nmetadata: []"])
def test_invalid_skill_metadata_is_rejected(tmp_path, frontmatter):
    directory = tmp_path / "review"
    directory.mkdir()
    (directory / "SKILL.md").write_text("---\n" + frontmatter + "\n---\nMethods", encoding="utf-8")
    with pytest.raises(ValueError):
        skill_runtime.catalog(tmp_path)


def test_oversized_skill_is_rejected_before_loading(tmp_path):
    directory = tmp_path / "review"
    directory.mkdir()
    (directory / "SKILL.md").write_text("x" * 64001, encoding="utf-8")
    with pytest.raises(ValueError, match="oversized"):
        skill_runtime.catalog(tmp_path)


async def test_dns_failure_is_explicit_unavailable(monkeypatch):
    async def broken(*args, **kwargs):
        raise OSError("private resolver detail")
    monkeypatch.setattr(official_web.asyncio.get_running_loop(), "getaddrinfo", broken)
    with pytest.raises(HTTPException) as error:
        await official_web.public_addresses("docs.example.com")
    assert error.value.status_code == 503 and "private" not in error.value.detail


async def test_enabled_skill_full_delivery_preserves_four_approvals(client, monkeypatch):
    from backend.models import Approval
    monkeypatch.setattr(get_settings(), "skill_runtime_enabled", True)

    p, run_id, approver = await preparing(client)
    try:
        csv = "name,email,department,role\n" + "\n".join(
            f"Member{i},skill{i}@example.com,设计部,{'admin' if i == 0 else 'member'}" for i in range(80))
        response = await client.post("/api/imports/validate", headers={"Idempotency-Key": uuid.uuid4().hex},
            json={"project_id": p["id"], "run_id": run_id, "csv_text": csv})
        assert response.status_code == 200 and response.json()["data"]["valid"]
        for _ in range(2):
            approval = next(a for a in (await approver.get(f"/api/approvals?run_id={run_id}")).json()["data"] if a["status"] == "pending")
            decided = await approver.post(f"/api/approvals/{approval['id']}/decision", headers={"Idempotency-Key": uuid.uuid4().hex},
                json={"decision": "approved", "expected_version": approval["version"]})
            assert decided.status_code == 200, decided.text
        async with SessionLocal() as session:
            run = await session.get(AgentRun, run_id)
            assert run.status == "succeeded" and run.state["acceptance_report"]["ready"]
            approvals = list(await session.scalars(select(Approval).where(Approval.run_id == run.id)))
            assert len(approvals) == 4 and all(a.status == "approved" for a in approvals)
            report = await session.scalar(select(ProjectArtifact).where(ProjectArtifact.run_id == run.id, ProjectArtifact.kind == "acceptance_notes"))
            assert json.loads(report.content)["evidence"]["actual_checks"]["ready"]
    finally:
        await approver.aclose()


async def test_official_reference_cannot_cross_chat_turns(client, web_settings, monkeypatch):
    chat = (await client.post("/api/chat/conversations", json={})).json()["data"]
    async def fetch(url, *args, **kwargs):
        return b'{"web":{"results":[{"url":"https://docs.example.com/"}]}}', url, "application/json"
    monkeypatch.setattr(official_web, "fetch_bytes", fetch)
    async with SessionLocal() as session:
        row = await session.get(ChatConversation, chat["id"])
        user = await actor_context(session, SimpleNamespace(actor_id=row.user_id, tenant_id=row.tenant_id))
        found = await call_tool(session, user, "search_official_docs", {
            "conversation_id": row.id, "product": "demo", "topic": "permissions"}, {})
        reference = found["result"]["results"][0]["reference"]
        await session.execute(update(ChatConversation).where(ChatConversation.id == row.id).values(version=row.version + 1))
        await session.commit()
        with pytest.raises(HTTPException) as error:
            await call_tool(session, user, "read_official_page", {"conversation_id": row.id, "reference": reference}, {})
        assert error.value.status_code == 403


async def test_provider_results_with_non_string_urls_are_ignored(web_settings, monkeypatch):
    async def fetch(*args, **kwargs):
        return b'{"web":{"results":[{"url":42},{"url":null},{}]}}', "https://api.search.brave.com/", "application/json"
    monkeypatch.setattr(official_web, "fetch_bytes", fetch)
    result = await official_web.search(SearchOfficialArguments(product="demo", topic="permissions"), "scope")
    assert result["status"] == "no_results" and not result["results"]
