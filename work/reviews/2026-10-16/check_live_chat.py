"""Exercise real MCP retrieval, SSE generation and history using disposable chat."""
import asyncio
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path(__file__).parent / "evidence"


async def main():
    credentials = json.loads((ROOT / "data/demo_workspace/credentials.json").read_bytes())["admin"]
    manifest = json.loads((ROOT / "data/demo_workspace/manifest.json").read_bytes())
    report = {"production_acceptance": False}
    chat = None
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8080", trust_env=False, timeout=180) as client:
        async def post(path, body):
            result = await client.post(path, json=body, headers={"X-CSRF-Token": client.cookies.get("saas_csrf", "")})
            result.raise_for_status()
            return result.json()

        await post("/api/auth/login", {"username": credentials["username"], "password": credentials["password"]})
        await post(f"/api/auth/spaces/{manifest['tenant_id']}/switch", {})
        chat = (await post("/api/chat/conversations", {}))["data"]
        try:
            query = "成员迁移流程是怎样的？请根据资料回答并引用来源。"
            for method, params in [("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                "clientInfo": {"name": "full-regression", "version": "1"}}), ("tools/list", {}),
                ("tools/call", {"name": "search_authorized_context", "arguments": {
                    "conversation_id": chat["id"], "query": query}})]:
                result = await post("/api/mcp", {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
                assert "result" in result and not result["result"].get("isError"), method
                if method == "tools/call":
                    sources = json.loads(result["result"]["content"][0]["text"])
                    assert sources
                    report["mcp"] = {"status": "passed", "sources": len(sources),
                                     "kinds": sorted({s.get("kind", "") for s in sources})}
            started = time.monotonic()
            events, kind, saved = {}, "", None
            client.headers["X-CSRF-Token"] = client.cookies["saas_csrf"]
            async with client.stream("POST", f"/api/chat/conversations/{chat['id']}/messages", json={
                "question": query, "expected_version": chat["version"], "request_id": str(uuid4()),
                "stream": True, "use_memory": False}) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("event: "):
                        kind = line[7:]
                    elif line.startswith("data: "):
                        data = json.loads(line[6:])
                        events[kind] = events.get(kind, 0) + 1
                        assert kind != "error", "SSE_GENERATION_FAILED"
                        if kind == "done":
                            saved = data["data"]
            assert saved and events.get("delta", 0) > 1
            message = saved["messages"][-1]
            assert message["mode"] == "llm" and message["citations"]
            assert "CSV" in message["answer"]
            assert any(s.get("retrieval") == "hybrid" for s in message["steps"])
            assert any(c.get("document_id") for c in message["citations"])
            fetched = await client.get(f"/api/chat/conversations/{chat['id']}")
            fetched.raise_for_status()
            assert fetched.json()["data"]["version"] == saved["version"]
            assert fetched.json()["data"]["messages"][-1]["answer"] == message["answer"]
            report["stream"] = {"status": "passed", "events": events,
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "citations": len(message["citations"]), "source_metadata_retained": True,
                "history_reload_matches": True, "generation_mode": message["mode"]}
        finally:
            if chat:
                response = await client.delete(f"/api/chat/conversations/{chat['id']}",
                    headers={"X-CSRF-Token": client.cookies.get("saas_csrf", "")})
                report["test_conversation_deleted"] = response.is_success
                response.raise_for_status()
            (EVIDENCE / "live-chat.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
