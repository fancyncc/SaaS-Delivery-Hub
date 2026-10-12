"""Authenticated, stateless MCP 2025-11-25 tools over Streamable HTTP.

The embedded agent shares the same dispatcher. Tenant/user identity is always
injected by the server, never accepted as a tool argument.
"""
import json
import logging
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.chat import evidence
from backend.chat_routes import customer, owned, require_unarchived
from backend.config import get_settings
from backend.db import get_session
from backend.rate_limit import enforce_rate_limit

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


class SearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: UUID
    query: str = Field(min_length=1, max_length=8000)


TOOLS = [{"name": "search_authorized_context", "description": "检索指定私有对话范围内的资料、平台说明和授权实时状态。只读。",
    "inputSchema": SearchArguments.model_json_schema(),
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}}]


async def authorized_conversation(session, identifier, user, *, version=None, permission="project.view"):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from sqlalchemy import select

    from backend.access import accessible_project_or_404, require_project_permission
    from backend.models import AuthSession
    from backend.worker import actor_context
    if user.session_id != "worker":
        valid = await session.scalar(select(AuthSession.id).where(AuthSession.id == user.session_id,
            AuthSession.user_id == user.user_id, AuthSession.tenant_id == user.tenant_id,
            AuthSession.revoked_at.is_(None), AuthSession.expires_at > datetime.now(UTC)))
        if not valid:
            raise HTTPException(401, "登录会话已失效")
    principal = await actor_context(session, SimpleNamespace(actor_id=user.user_id, tenant_id=user.tenant_id))
    row = await owned(session, identifier, principal, hydrate_history=False)
    await session.refresh(row, attribute_names=["version", "archived", "project_id", "tool_state", "tool_state_version"])
    require_unarchived(row)
    if version is not None and row.version != version:
        raise HTTPException(409, "对话在工具调用期间已更新，本次结果未保存")
    if row.project_id:
        project = await accessible_project_or_404(session, row.project_id, principal)
        await require_project_permission(session, project, principal, permission)
    return row


async def call_tool(session, user, name, arguments, api_schema):
    if name == TOOLS[0]["name"]:
        args = SearchArguments.model_validate(arguments)
        row = await authorized_conversation(session, args.conversation_id, user)
        version = row.version
        result = await evidence(session, row, user, args.query, api_schema)
        await authorized_conversation(session, row.id, user, version=version)
        return result
    from backend.access import accessible_project_or_404, require_project_permission
    from backend.tool_registry import available, execute
    if name not in available():
        raise ValueError("unknown tool")
    arguments = dict(arguments)
    identifier = UUID(str(arguments.pop("conversation_id", "")))
    row = await authorized_conversation(session, identifier, user, permission=available()[name].permission)
    project = None
    if row.project_id:
        project = await accessible_project_or_404(session, row.project_id, user)
        await require_project_permission(session, project, user, available()[name].permission)
    elif not available()[name].external:
        raise HTTPException(403, "业务检查工具需要绑定项目的对话")
    # Per-turn MCP budget survives independent HTTP calls and reserves before network I/O.
    from sqlalchemy import select, update

    from backend.chat_models import ChatConversation
    locked = await session.scalar(select(ChatConversation).where(ChatConversation.id == row.id).with_for_update())
    version = locked.version
    available()[name].parameters.model_validate(arguments)
    state = dict(locked.tool_state)
    turn = str(locked.version)
    counts = dict(state.get("counts", {})) if state.get("turn") == turn else {}
    key = name
    if counts.get(key, 0) >= available()[name].limit:
        raise HTTPException(429, "本轮可选工具额度已耗尽，请在下一轮提问时重试")
    counts[key] = counts.get(key, 0) + 1
    state = {"turn": turn, "counts": counts, "calls": state.get("calls", [])[-8:]}
    changed = await session.execute(update(ChatConversation).where(ChatConversation.id == row.id,
        ChatConversation.tenant_id == user.tenant_id, ChatConversation.user_id == user.user_id,
        ChatConversation.version == version, ChatConversation.archived.is_(False),
        ChatConversation.tool_state_version == locked.tool_state_version).values(
            tool_state=state, tool_state_version=locked.tool_state_version + 1))
    if not changed.rowcount:
        raise HTTPException(409, "工具调用额度发生并发冲突，请重新读取对话")
    await session.commit()  # Reserve before network I/O; a failed response cannot reset the budget.
    result = await execute(session, project, name, arguments,
                           scope=f"chat:{user.tenant_id}:{user.user_id}:{row.id}:turn:{version}")
    locked = await authorized_conversation(session, row.id, user, version=version, permission=available()[name].permission)
    state = dict(locked.tool_state)
    state["calls"] = [*state.get("calls", []), {"tool": name, "version": "1", "output": result}][-8:]
    changed = await session.execute(update(ChatConversation).where(ChatConversation.id == row.id,
        ChatConversation.tenant_id == user.tenant_id, ChatConversation.user_id == user.user_id,
        ChatConversation.version == version, ChatConversation.archived.is_(False),
        ChatConversation.tool_state_version == locked.tool_state_version).values(
            tool_state=state, tool_state_version=locked.tool_state_version + 1))
    if not changed.rowcount:
        raise HTTPException(409, "工具结果发生并发冲突，来源未保存")
    await session.commit()
    return result


def tool_list():
    from backend.tool_registry import available
    additions = []
    for name, spec in available().items():
        schema = spec.parameters.model_json_schema()
        schema["properties"]["conversation_id"] = {"type": "string", "format": "uuid"}
        schema["required"] = [*schema.get("required", []), "conversation_id"]
        additions.append({"name": name, "description": spec.description, "inputSchema": schema,
            "annotations": {"readOnlyHint": True, "destructiveHint": False,
                "idempotentHint": False, "openWorldHint": spec.external}})
    return TOOLS + additions


@router.get("")
@router.delete("")
async def unsupported():
    return Response(status_code=405)


@router.post("")
async def rpc(request: Request, user=Depends(customer), session=Depends(get_session)):
    origin = request.headers.get("origin")
    allowed = {value.strip().rstrip('/') for value in get_settings().cors_origins.split(',')}
    allowed.add(get_settings().frontend_base_url.rstrip('/'))
    if origin and origin.rstrip('/') not in allowed:
        raise HTTPException(403, "Origin not allowed")
    await enforce_rate_limit(f"mcp:{user.tenant_id}:{user.user_id}", 30, 60)
    try:
        body = await request.json()
    except ValueError:
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0" or not isinstance(body.get("method"), str):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    identifier = body.get("id")
    if identifier is not None and type(identifier) not in {str, int}:
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    if "id" not in body:
        return Response(status_code=202)
    method, params = body["method"], body.get("params", {})
    envelope = {"jsonrpc": "2.0", "id": identifier}
    if not isinstance(params, dict):
        return {**envelope, "error": {"code": -32602, "message": "Invalid params"}}
    if method == "initialize":
        return {**envelope, "result": {"protocolVersion": "2025-11-25", "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "saas-authorized-context", "version": "1.0.0"}}}
    if request.headers.get("MCP-Protocol-Version", "2025-11-25") != "2025-11-25":
        raise HTTPException(400, "Unsupported MCP protocol version")
    if method == "ping":
        return {**envelope, "result": {}}
    if method == "tools/list":
        return {**envelope, "result": {"tools": tool_list()}}
    if method == "tools/call":
        try:
            result = await call_tool(session, user, params.get("name"), params.get("arguments", {}), request.app.openapi())
        except (ValueError, ValidationError):
            return {**envelope, "error": {"code": -32602, "message": "Invalid tool name or arguments"}}
        except (HTTPException, httpx.HTTPError, TimeoutError):
            logging.getLogger(__name__).exception("MCP authorized retrieval failed")
            return {**envelope, "result": {"isError": True, "content": [{"type": "text", "text": "工具不可用或无权访问该范围"}]}}
        return {**envelope, "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}], "isError": False}}
    return {**envelope, "error": {"code": -32601, "message": "Method not found"}}
