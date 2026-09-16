import json

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from backend.config import get_settings
from backend.db import SessionLocal
from backend.intelligence import ExtractedRequirements, structured
from backend.knowledge_routes import retrieve
from backend.models import KnowledgeDocument, Tenant


async def test_structured_retries_invalid_model_output(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "model_api_style", "responses")
    monkeypatch.setattr(settings, "model_base_url", "https://model.example.test/v1")
    monkeypatch.setattr(settings, "model_name", "test-model")
    monkeypatch.setattr(settings, "model_api_key", "test-only")
    attempts = []
    def handler(request):
        attempts.append(request)
        payload = json.loads(request.content)
        assert payload["store"] is False
        assert "Tool Result" in payload["instructions"]
        assert "Memory" in payload["instructions"]
        return httpx.Response(200, json={"status": "completed", "output": [{"content": [{"type": "output_text", "text": "{}"}]}]})
    with pytest.raises(HTTPException) as error:
        await structured("test", {}, ExtractedRequirements, transport=httpx.MockTransport(handler))
    assert error.value.status_code == 422 and len(attempts) == 3


async def test_model_without_credentials_is_not_silent_fallback(monkeypatch):
    monkeypatch.setattr(get_settings(), "model_api_key", "")
    with pytest.raises(HTTPException) as error:
        await structured("test", {}, ExtractedRequirements)
    assert error.value.status_code == 503


async def test_knowledge_tenant_and_version_boundaries(client):
    base = {"title": "成员导入", "version": 1, "module": "import", "source": "内部原创", "license": "本项目授权测试资料", "body": "成员导入需要校验部门、角色和邮箱，审批通过后才允许创建成员。"}
    first = await client.post("/api/knowledge", json=base)
    assert first.status_code == 200
    second = await client.post("/api/knowledge", json={**base, "version": 2, "body": "成员导入需要有效的 CSV 文件和独立审批，完成后核对实际成员数量。"})
    assert second.status_code == 200
    assert (await client.post("/api/knowledge", json=base)).status_code == 409
    async with SessionLocal() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == "legacy-demo"))
        other = Tenant(name="其他公司", slug="knowledge-other")
        session.add(other)
        await session.flush()
        session.add(KnowledgeDocument(tenant_id=other.id, **{**base, "body": "跨公司秘密：成员导入资料绝不能泄露给其他租户读取。"}))
        await session.commit()
        hits = await retrieve(session, tenant.id, "成员导入")
        assert len(hits) == 1 and hits[0]["version"] == 2
        assert "秘密" not in hits[0]["text"]
    await client.post(f"/api/knowledge/{second.json()['data']['id']}/deactivate")
    async with SessionLocal() as session:
        assert await retrieve(session, tenant.id, "成员导入") == []
