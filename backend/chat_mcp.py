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
from backend.chat_routes import customer, owned
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


async def call_tool(session, user, name, arguments, api_schema):
    if name != TOOLS[0]["name"]:
        raise ValueError("unknown tool")
    args = SearchArguments.model_validate(arguments)
    row = await owned(session, args.conversation_id, user)
    return await evidence(session, row, user, args.query, api_schema)


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
        return {**envelope, "result": {"tools": TOOLS}}
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
