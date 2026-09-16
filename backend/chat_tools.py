"""Bounded native function calling; model chooses queries, server fixes scope."""
import json

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.chat_mcp import call_tool
from backend.config import get_settings
from backend.prompt_boundary import DATA_BOUNDARY


class QueryArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=4000)


async def tool_context(session, row, user, query, schema):
    """At most two tool rounds. Output consists only of validated observations."""
    s = get_settings()
    if s.model_api_style != "chat_completions" or s.model_mode != "real":
        result = await call_tool(session, user, "search_authorized_context", {"conversation_id": row.id, "query": query}, schema)
        yield {"sources": result, "round": 1}
        return
    local = s.llm_mode == "local"
    base = s.llm_local_base_url if local else s.model_base_url
    key = s.llm_local_api_key if local else s.model_api_key
    model = s.llm_local_model if local else s.model_name
    if not base or not model or (not local and not key):
        raise HTTPException(503, "工具规划模型尚未配置")
    tool = {"type": "function", "function": {"name": "search_authorized_context",
        "description": "查询本轮固定授权范围内的资料和实时状态。可用更具体的关键词补充一次检索。",
        "parameters": QueryArguments.model_json_schema()}}
    messages = [{"role": "system", "content": "先调用检索工具。观察结果后，仅在缺少必要资料时再检索一次；资料足够则停止调用。"
        + DATA_BOUNDARY + "只读，不执行或声称执行业务动作。不要输出内部推理。"},
        {"role": "user", "content": query}]
    async with httpx.AsyncClient(timeout=s.model_timeout) as client:
        for round_number in range(1, 3):
            response = await client.post(base.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {key}"} if key else {},
                json={"model": model, "stream": False, "messages": messages, "tools": [tool],
                    "tool_choice": {"type": "function", "function": {"name": "search_authorized_context"}} if round_number == 1 else "auto",
                    "max_tokens": 1024})
            if response.status_code in {401, 403}:
                raise HTTPException(503, "生成模型认证失败，请检查 MODEL_API_KEY 和 MODEL_BASE_URL；本次回答未保存")
            response.raise_for_status()
            message = response.json()["choices"][0]["message"]
            calls = message.get("tool_calls", [])
            if not calls:
                if round_number == 1:
                    # Some compatible gateways ignore tools/tool_choice. The
                    # mandatory read is server-owned; never parse model prose
                    # as a command, query, scope override, or authorization.
                    sources = await call_tool(session, user, "search_authorized_context",
                        {"conversation_id": row.id, "query": query}, schema)
                    yield {"sources": sources, "round": 1, "tool": "search_authorized_context",
                           "planning_mode": "server_fallback"}
                return
            if len(calls) != 1:
                raise HTTPException(502, "模型工具调用超过本轮预算")
            call = calls[0]
            if call["function"]["name"] != "search_authorized_context":
                raise HTTPException(502, "模型请求了未授权工具")
            args = QueryArguments.model_validate_json(call["function"]["arguments"])
            # Original scope references survive all model-generated queries.
            sources = await call_tool(session, user, call["function"]["name"],
                {"conversation_id": row.id, "query": (query + "\n" + args.query)[:8000]}, schema)
            yield {"sources": sources, "round": round_number, "tool": call["function"]["name"]}
            if sources and sources[0].get("kind") == "scope":
                return
            messages.append({"role": "assistant", "content": None, "tool_calls": calls})
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(sources, ensure_ascii=False)})
