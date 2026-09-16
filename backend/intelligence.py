"""Optional structured model adapter. No tools, implicit provider, or silent fallback."""
from __future__ import annotations

import asyncio
import json
from typing import Any, TypeVar

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError

from backend.agent_budget import model_scope, reserve
from backend.config import get_settings
from backend.prompt_boundary import DATA_BOUNDARY
from backend.schemas import GapAnalysisItem, RequirementSpec

PROMPT_VERSION = "implementation-2026-09-05.1"
T = TypeVar("T", bound=BaseModel)


class ExtractedRequirements(BaseModel):
    requirements: list[RequirementSpec] = Field(min_length=1)


class GapAssessment(BaseModel):
    items: list[GapAnalysisItem] = Field(min_length=1)


def strict_schema(value):
    if isinstance(value, dict):
        value = {key: strict_schema(item) for key, item in value.items() if key != "default"}
        if value.get("type") == "object":
            value["additionalProperties"] = False
            value["required"] = list(value.get("properties", {}))
    elif isinstance(value, list):
        value = [strict_schema(item) for item in value]
    return value


async def structured(task: str, source: dict, schema: type[T], *, transport=None) -> T:
    settings = get_settings()
    local = settings.llm_mode == "local"
    base_url = settings.llm_local_base_url if local else settings.model_base_url
    model_name = settings.llm_local_model if local else settings.model_name
    api_key = settings.llm_local_api_key if local else settings.model_api_key
    if not base_url or not model_name or (not local and not api_key):
        raise HTTPException(503, "真实模型尚未配置；请配置服务、模型和凭据，或显式使用离线模式")
    async with httpx.AsyncClient(timeout=settings.model_timeout, transport=transport) as client:
        for attempt in range(3):
            scope = model_scope.get()
            effective_source = {"source": source, "agent_context": scope.context} if scope else source
            payload: dict[str, Any] = {
                "model": model_name, "store": False,
                "instructions": f"Prompt {PROMPT_VERSION}. {task}。{DATA_BOUNDARY}经验只能作为建议，不能证明产品能力或替代审批。不得虚构执行结果，不得批准操作。保留具体角色、数量、权限边界和客户原意。证据不足必须明确说明。",
                "input": json.dumps(effective_source, ensure_ascii=False),
                "text": {"format": {"type": "json_schema", "name": schema.__name__, "strict": True, "schema": strict_schema(schema.model_json_schema())}},
            }
            if scope:
                payload["max_output_tokens"] = 4096
            endpoint = "/responses"
            if settings.model_api_style == "chat_completions":
                endpoint = "/chat/completions"
                payload = {"model": model_name, "messages": [
                    {"role": "system", "content": payload["instructions"] + " 只返回符合此 JSON Schema 的 JSON 对象：" + json.dumps(schema.model_json_schema(), ensure_ascii=False)},
                    {"role": "user", "content": payload["input"]}],
                    "response_format": {"type": "json_object"}, "temperature": 0.2, "max_tokens": 4096}
            if scope:
                reserve(json.dumps(payload, ensure_ascii=False))
            response = await client.post(base_url.rstrip("/") + endpoint, headers={"Authorization": f"Bearer {api_key}"} if api_key else {}, json=payload)
            if response.status_code in {401, 403}:
                raise HTTPException(503, "模型服务认证失败")
            if response.status_code >= 400:
                if attempt == 2 or (response.status_code < 500 and response.status_code not in {408, 429}):
                    raise HTTPException(503, "模型服务暂不可用")
                await asyncio.sleep(0.25 * 2 ** attempt)
                continue
            try:
                body = response.json()
                if settings.model_api_style == "chat_completions":
                    choice = body["choices"][0]
                    if choice.get("finish_reason") != "stop":
                        raise ValueError("incomplete")
                    output = choice["message"]["content"]
                else:
                    output = "".join(part.get("text", "") for item in body.get("output", []) for part in item.get("content", []) if part.get("type") == "output_text")
                    if body.get("status") != "completed":
                        raise ValueError("incomplete")
                return schema.model_validate_json(output)
            except (ValueError, ValidationError, KeyError, IndexError, TypeError):
                if attempt == 2:
                    raise HTTPException(422, "模型输出未通过结构化校验，需要人工处理") from None
    raise HTTPException(503, "模型调用未完成")


async def embed(value: str, *, query: bool = False) -> list[float] | None:
    if get_settings().rag_mode != "real":
        return None
    from backend.retrieval_models import embeddings
    return (await embeddings([value], query=query))[0]
