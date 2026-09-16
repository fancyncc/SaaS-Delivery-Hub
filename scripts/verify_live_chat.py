"""Local acceptance probe using a temporary session and conversation, cleaned up.

Requires the private owner env file. Prints counts/status only, never credentials,
source text, user names or model answers. Does not change user passwords.
"""
import asyncio
import json
import sys
import time
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.chat_models import ChatConversation
from backend.config import Settings
from backend.models import AuthSession, KnowledgeDocument, Tenant, TenantMembership, User
from backend.security import create_session


async def main():
    s = Settings(_env_file=".env.local-rag-owner")
    engine = create_async_engine(s.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    conversation_id = None
    auth_id = None
    try:
        async with factory() as session:
            member = await session.scalar(select(TenantMembership).where(
                TenantMembership.company_role_code == "company_admin", TenantMembership.status == "active",
                TenantMembership.tenant_id.in_(select(KnowledgeDocument.tenant_id))))
            assert member, "No customer administrator available for acceptance"
            user = await session.get(User, member.user_id)
            tenant = await session.get(Tenant, member.tenant_id)
            token, csrf, auth = await create_session(session, user, tenant, "local-chat-acceptance")
            auth_id = auth.id
            await session.commit()
        transport = None
        if "--in-process" in sys.argv:
            from backend.main import app
            transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=180, transport=transport,
                cookies={"saas_session": token, "saas_csrf": csrf}, headers={"X-CSRF-Token": csrf}) as client:
            response = await client.post('/api/chat/conversations', json={})
            assert response.status_code == 200, f"conversation create HTTP {response.status_code}"
            chat = response.json()["data"]
            conversation_id = chat["id"]
            for method, params in [("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "local-probe", "version": "1"}}), ("tools/list", {})]:
                response = await client.post('/api/mcp', json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
                assert response.status_code == 200 and "result" in response.json(), f"MCP {method} failed"
            start = time.monotonic()
            probe = await client.post('/api/mcp', json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "search_authorized_context", "arguments": {"conversation_id": conversation_id, "query": "项目验收和成员导入要求"}}})
            probe_result = probe.json()['result']
            assert not probe_result.get('isError'), 'MCP hybrid retrieval failed'
            sources = json.loads(probe_result['content'][0]['text'])
            assert any(source.get('kind') == 'customer_document' for source in sources)
            print(json.dumps({"mcp_hybrid_verified": True, "sources": len(sources), "retrieval_seconds": round(time.monotonic() - start, 2)}), flush=True)
            if "--retrieval-only" in sys.argv:
                return
            start = time.monotonic()
            counts, first_delta, result = {}, None, None
            async with client.stream('POST', f'/api/chat/conversations/{conversation_id}/messages', json={
                "question": "项目验收和成员导入有哪些要求？请引用资料。", "expected_version": chat["version"],
                "request_id": str(uuid4()), "stream": True}) as response:
                assert response.status_code == 200, f"stream HTTP {response.status_code}"
                kind = ""
                async for line in response.aiter_lines():
                    if line.startswith('event: '):
                        kind = line[7:]
                    if line.startswith('data: '):
                        data = json.loads(line[6:])
                        counts[kind] = counts.get(kind, 0) + 1
                        if kind == 'delta' and first_delta is None:
                            first_delta = round(time.monotonic() - start, 2)
                        if kind == 'error':
                            raise RuntimeError(f"stream error: {data.get('message')}")
                        if kind == 'done':
                            result = data['data']
            assert result and counts.get('delta', 0) > 1
            message = result['messages'][-1]
            assert message['mode'] == 'llm' and message['citations']
            assert any(step.get('retrieval') == 'hybrid' for step in message['steps'])
            fetched = await client.get(f'/api/chat/conversations/{conversation_id}')
            assert fetched.status_code == 200 and fetched.json()['data']['version'] == result['version']
            print(json.dumps({"verified": True, "events": counts, "first_token_seconds": first_delta,
                "total_seconds": round(time.monotonic() - start, 2), "citations": len(message['citations']),
                "tool_rounds": sum(step['stage'] == 'tool' for step in message['steps']), "model": "qwen-plus", "retrieval": "hybrid"}))
    finally:
        async with factory() as session:
            if conversation_id:
                await session.execute(delete(ChatConversation).where(ChatConversation.id == conversation_id))
            if auth_id:
                await session.execute(delete(AuthSession).where(AuthSession.id == auth_id))
            await session.commit()
        await engine.dispose()


if __name__ == "__main__":
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(main())
