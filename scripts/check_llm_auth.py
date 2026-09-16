"""Send one tiny request using the backend configuration; never print the key."""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from backend.config import get_settings


def main():
    s = get_settings()
    local = s.llm_mode == "local"
    base = s.llm_local_base_url if local else s.model_base_url
    key = s.llm_local_api_key if local else s.model_api_key
    model = s.llm_local_model if local else s.model_name

    def redact(text):
        if key:
            text = text.replace(key, "<redacted>")
        return re.sub(r"sk-[A-Za-z0-9_-]+", "<redacted>", text)

    if not base or not model or (not local and not key):
        print("Configuration missing: base URL, model, or API key.")
        return 2
    chat = s.model_api_style == "chat_completions"
    url = base.rstrip("/") + ("/chat/completions" if chat else "/responses")
    payload = {"model": model, "stream": True}
    if chat:
        payload.update(messages=[{"role": "user", "content": "Reply only OK."}], max_tokens=16)
    else:
        payload.update(input="Reply only OK.", max_output_tokens=16, store=False)
    if "--tools" in sys.argv:
        if not chat:
            print("Tool probe requires chat_completions.")
            return 2
        from backend.chat_tools import QueryArguments
        payload.pop("stream")
        payload.update(max_tokens=1024, tools=[{"type": "function", "function": {
            "name": "search_authorized_context", "description": "Search authorized context",
            "parameters": QueryArguments.model_json_schema()}}],
            tool_choice={"type": "function", "function": {"name": "search_authorized_context"}})
        if "--no-stream" in sys.argv:
            payload["stream"] = False
    print(redact(json.dumps({"url": url, "model": model, "api_style": s.model_api_style,
                            "key_configured": bool(key)}, ensure_ascii=True)))
    try:
        with httpx.Client(timeout=s.model_timeout) as client:
            with client.stream("POST", url, headers={"Authorization": f"Bearer {key}"} if key else {},
                               json=payload) as response:
                print(f"HTTP status: {response.status_code}")
                print("Content-Type: " + response.headers.get("content-type", "<missing>"))
                if response.is_error:
                    response.read()
                    print("Error body: " + redact(response.text)[:4000])
                    return 1
                if "--tools" in sys.argv:
                    response.read()
                    print("Body preview: " + redact(response.text)[:2000])
                    try:
                        body = response.json()
                        calls = body["choices"][0]["message"].get("tool_calls", [])
                        print("Tool calls returned: " + str(len(calls)))
                        return 0 if calls else 1
                    except (ValueError, KeyError, IndexError, TypeError) as exc:
                        print("Invalid tool response: " + type(exc).__name__)
                        return 1
                print("Response preview:")
                size = 0
                for line in response.iter_lines():
                    if line:
                        print(redact(line)[:1000])
                        size += len(line)
                        if size >= 2000 or line == "data: [DONE]":
                            break
                return 0
    except httpx.RequestError as exc:
        print("Network error (no HTTP status): " + redact(str(exc)))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
