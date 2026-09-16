"""Provider token streaming with explicit completion and bounded output."""
import json

import httpx
from fastapi import HTTPException

from backend.config import get_settings
from backend.prompt_boundary import DATA_BOUNDARY


async def stream_text(instructions, source, *, transport=None):
    instructions = instructions + "\n" + DATA_BOUNDARY
    s = get_settings()
    local = s.llm_mode == "local"
    base = s.llm_local_base_url if local else s.model_base_url
    model = s.llm_local_model if local else s.model_name
    key = s.llm_local_api_key if local else s.model_api_key
    if not base or not model or (not local and not key):
        raise HTTPException(503, "生成模型尚未配置")
    content = json.dumps(source, ensure_ascii=False)
    if s.model_api_style == "chat_completions":
        endpoint = "/chat/completions"
        payload = {"model": model, "stream": True, "max_tokens": 4096,
            "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": content}]}
    else:
        endpoint = "/responses"
        payload = {"model": model, "stream": True, "store": False, "max_output_tokens": 4096,
            "instructions": instructions, "input": content}
    complete, count = False, 0
    async with httpx.AsyncClient(timeout=s.model_timeout, transport=transport) as client:
        async with client.stream("POST", base.rstrip("/") + endpoint,
                headers={"Authorization": f"Bearer {key}"} if key else {}, json=payload) as response:
            if response.status_code in {401, 403}:
                raise HTTPException(503, "生成模型认证失败，请检查 MODEL_API_KEY 和 MODEL_BASE_URL；本次回答未保存")
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    break
                data = json.loads(raw)
                if "error" in data:
                    raise HTTPException(502, "模型流返回错误")
                delta = ""
                if s.model_api_style == "chat_completions":
                    for choice in data.get("choices", []):
                        if choice.get("index", 0) != 0:
                            continue
                        delta += choice.get("delta", {}).get("content") or ""
                        reason = choice.get("finish_reason")
                        if reason and reason != "stop":
                            raise HTTPException(502, "模型回答未完整生成")
                        complete = complete or reason == "stop"
                else:
                    if data.get("type") == "response.output_text.delta":
                        delta = data["delta"]
                    if data.get("type") in {"response.failed", "response.incomplete", "error"}:
                        raise HTTPException(502, "模型回答未完整生成")
                    complete = complete or data.get("type") == "response.completed"
                if delta:
                    count += len(delta)
                    if count > 16000:
                        raise HTTPException(502, "模型回答超过长度限制")
                    yield delta
    if not complete or not count:
        raise HTTPException(502, "模型流中断，本次回答未保存")
