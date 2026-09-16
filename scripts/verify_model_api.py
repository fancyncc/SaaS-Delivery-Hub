"""Probe the configured Chat Completions provider without exposing credentials.

Makes small real (potentially billed) requests; stops at the first failure.
Run on the host or pipe this file to `docker compose exec -T api python -`.
"""
import asyncio
import json
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path.cwd()))

from backend.config import get_settings
from backend.llm_stream import stream_text


def check_response(response):
    if response.is_success:
        return
    # Never print arbitrary provider content: it may echo credentials or inputs.
    match = re.search(r"request id[:：]\s*([A-Za-z0-9_-]{1,100})", response.text)
    result = {"http_status": response.status_code,
              "category": "authentication" if response.status_code == 401 else
              "access_denied" if response.status_code == 403 else "provider_error"}
    if "Invalid token" in response.text:
        result["provider_error"] = "Invalid token"
    if match:
        result["request_id"] = match.group(1)
    raise RuntimeError(json.dumps(result))


async def main():
    s = get_settings()
    local = s.llm_mode == "local"
    base = s.llm_local_base_url if local else s.model_base_url
    key = s.llm_local_api_key if local else s.model_api_key
    model = s.llm_local_model if local else s.model_name
    if s.model_api_style != "chat_completions" or not base or not model or (not local and not key):
        raise RuntimeError("Chat Completions URL/model/credential configuration incomplete")
    print(json.dumps({"model": model, "api_style": s.model_api_style,
                      "credential_present": bool(key)}, ensure_ascii=True), flush=True)
    async with httpx.AsyncClient(timeout=s.model_timeout) as client:
        endpoint = base.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        response = await client.post(endpoint, headers=headers, json={
            "model": model, "messages": [{"role": "user", "content": "Reply OK."}]})
        check_response(response)
        assert response.json()["choices"][0]["message"].get("content"), "Empty completion"
        print("chat_completions: passed", flush=True)
        response = await client.post(endpoint, headers=headers, json={
            "model": model, "messages": [{"role": "user", "content": "Call check_connection."}],
            "tools": [{"type": "function", "function": {"name": "check_connection",
                "parameters": {"type": "object", "properties": {}}}}],
            "tool_choice": {"type": "function", "function": {"name": "check_connection"}},
            "max_tokens": 128})
        check_response(response)
        calls = response.json()["choices"][0]["message"].get("tool_calls", [])
        assert len(calls) == 1 and calls[0]["function"]["name"] == "check_connection", "Missing tool call"
        print("native_tool_call: passed", flush=True)
    chunks = [chunk async for chunk in stream_text("Reply with a short greeting.", {"question": "Hello"})]
    assert chunks and "".join(chunks).strip(), "Empty stream"
    print(json.dumps({"stream": "passed", "deltas": len(chunks), "ready": True}), flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as exc:
        print(f"ready: false; {exc}")
        raise SystemExit(1) from None
    except Exception as exc:
        print(f"ready: false; error_type: {type(exc).__name__}")
        raise SystemExit(1) from None
