"""Private scoped memory and explicit candidate acceptance. No implicit writes."""
import re
import time
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.access import accessible_project_or_404, require_project_permission
from backend.chat_models import (
    MemoryCandidate,
    MemoryItem,
    MemoryPreference,
)
from backend.chat_routes import customer, owned
from backend.config import get_settings
from backend.conversation_state import digest, message_id
from backend.db import get_session
from backend.security import Principal

router = APIRouter(prefix="/api/chat", tags=["chat memory"])


def enabled():
    if not get_settings().chat_memory_items_enabled:
        raise HTTPException(404, "结构化记忆尚未启用")


def candidates_enabled():
    enabled()
    if not get_settings().chat_memory_candidates_enabled:
        raise HTTPException(404, "记忆候选尚未启用")


def visible(user):
    return (MemoryItem.user_id == user.user_id) & or_(MemoryItem.tenant_id == user.tenant_id,
        (MemoryItem.scope == "user") & MemoryItem.tenant_id.is_(None))


def view(item):
    return {key: getattr(item, key) for key in ("id", "scope", "scope_id", "category", "key", "content", "source", "status", "version", "expires_at")}


async def authorize_scope(session, user, scope, identifier, category):
    if scope == "user":
        if category != "preference":
            raise HTTPException(422, "跨空间记忆仅允许显式通用偏好")
        return None, user.user_id
    if scope == "workspace":
        if identifier and identifier != user.tenant_id:
            raise HTTPException(404, "空间不存在")
        return user.tenant_id, user.tenant_id
    if not identifier:
        raise HTTPException(422, "请选择项目或对话")
    if scope == "project":
        project = await accessible_project_or_404(session, identifier, user)
        await require_project_permission(session, project, user, "project.view")
    else:
        await owned(session, identifier, user)
    return user.tenant_id, identifier


async def authorized_item(session, user, identifier):
    item = await session.scalar(select(MemoryItem).where(visible(user), MemoryItem.id == str(identifier)))
    if item is None:
        raise HTTPException(404, "记忆不存在")
    await authorize_scope(session, user, item.scope, item.scope_id, item.category)
    return item


async def sync_legacy(session, user, content, version):
    row = await session.scalar(select(MemoryItem).where(visible(user), MemoryItem.scope == "workspace",
        MemoryItem.scope_id == user.tenant_id, MemoryItem.key == "__legacy_workspace__"))
    if row is None:
        row = MemoryItem(user_id=user.user_id, tenant_id=user.tenant_id, scope="workspace", scope_id=user.tenant_id,
            category="legacy", key="__legacy_workspace__", source={"kind": "legacy_import"})
        session.add(row)
    row.content, row.version, row.status = content, version, "active" if content else "revoked"


async def effective_memory(session, user, conversation, legacy):
    if not get_settings().chat_memory_items_enabled:
        return legacy
    rows = list(await session.scalars(select(MemoryItem).where(visible(user), MemoryItem.status == "active",
        or_(MemoryItem.expires_at.is_(None), MemoryItem.expires_at > time.time()))))
    allowed = {("user", user.user_id), ("workspace", user.tenant_id), ("conversation", conversation.id)}
    if conversation.project_id:
        allowed.add(("project", conversation.project_id))
    priority = {"user": 0, "workspace": 1, "project": 2, "conversation": 3}
    selected = {}
    for item in sorted(rows, key=lambda r: priority[r.scope]):
        if (item.scope, item.scope_id) in allowed:
            selected[item.key] = {"scope": item.scope, "key": item.key, "content": item.content}
    if "__legacy_workspace__" not in selected and legacy:
        selected["__legacy_workspace__"] = {"scope": "workspace", "content": legacy}
    import json
    return json.dumps(list(selected.values()), ensure_ascii=False)


class ItemCreate(BaseModel):
    scope: Literal["user", "workspace", "project", "conversation"] = "workspace"
    scope_id: str | None = None
    category: Literal["preference", "background", "constraint"] = "preference"
    key: str = Field(min_length=1, max_length=100, pattern=r"^[\w.-]+$")
    content: str = Field(min_length=1, max_length=4000)
    expires_at: float | None = None


class ItemUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=4000)
    expires_at: float | None = None


async def commit(session):
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "相同作用域已有此记忆键，请刷新并明确修改已有条目") from None


@router.get("/memory-items")
async def list_items(user: Principal = Depends(customer), session: AsyncSession = Depends(get_session)):
    enabled()
    rows = list(await session.scalars(select(MemoryItem).where(visible(user), MemoryItem.status == "active")))
    result = []
    for row in rows:
        try:
            await authorize_scope(session, user, row.scope, row.scope_id, row.category)
        except HTTPException:
            continue
        result.append(view(row))
    return {"data": result}


@router.post("/memory-items")
async def create_item(payload: ItemCreate, user: Principal = Depends(customer), session: AsyncSession = Depends(get_session)):
    enabled()
    if payload.key.startswith("__") or not payload.content.strip():
        raise HTTPException(422, "记忆内容或键无效")
    tenant, scope_id = await authorize_scope(session, user, payload.scope, payload.scope_id, payload.category)
    previous = await session.scalar(select(MemoryItem).where(MemoryItem.user_id == user.user_id,
        MemoryItem.scope == payload.scope, MemoryItem.scope_id == scope_id, MemoryItem.key == payload.key))
    if previous and previous.status == "revoked":
        changed = await session.execute(update(MemoryItem).where(MemoryItem.id == previous.id,
            MemoryItem.version == previous.version, MemoryItem.status == "revoked").values(
                category=payload.category, content=payload.content.strip(), expires_at=payload.expires_at,
                source={"kind": "user_confirmed"}, status="active", version=previous.version + 1))
        if changed.rowcount != 1:
            raise HTTPException(409, "记忆已变更，请刷新")
        await commit(session)
        return {"data": view(previous)}
    row = MemoryItem(user_id=user.user_id, tenant_id=tenant, scope=payload.scope, scope_id=scope_id,
        category=payload.category, key=payload.key, content=payload.content.strip(), expires_at=payload.expires_at,
        source={"kind": "user_confirmed"})
    session.add(row)
    await commit(session)
    return {"data": view(row)}


@router.patch("/memory-items/{identifier}")
async def edit_item(identifier: UUID, payload: ItemUpdate, user: Principal = Depends(customer), session: AsyncSession = Depends(get_session)):
    enabled()
    row = await authorized_item(session, user, identifier)
    if row.category == "legacy":
        raise HTTPException(409, "旧记忆请通过兼容记忆编辑框修改")
    if not payload.content.strip():
        raise HTTPException(422, "内容不能为空")
    changed = await session.execute(update(MemoryItem).where(MemoryItem.id == row.id, MemoryItem.version == payload.expected_version)
        .values(content=payload.content.strip(), source={"kind": "user_confirmed"}, expires_at=payload.expires_at, version=payload.expected_version + 1))
    if changed.rowcount != 1:
        raise HTTPException(409, "记忆已变更，请刷新")
    await commit(session)
    return {"data": view(row)}


@router.delete("/memory-items/{identifier}")
async def delete_item(identifier: UUID, expected_version: int, user: Principal = Depends(customer), session: AsyncSession = Depends(get_session)):
    enabled()
    row = await authorized_item(session, user, identifier)
    if row.category == "legacy":
        raise HTTPException(409, "旧记忆请通过兼容记忆编辑框清空")
    changed = await session.execute(update(MemoryItem).where(MemoryItem.id == row.id, MemoryItem.version == expected_version)
        .values(status="revoked", version=expected_version + 1))
    if changed.rowcount != 1:
        raise HTTPException(409, "记忆已变更，请刷新")
    await commit(session)
    return {"data": {"deleted": True}}


class PreferenceUpdate(BaseModel):
    expected_version: int = Field(ge=0)
    auto_extract: bool


@router.get("/memory-preferences")
async def preferences(user: Principal = Depends(customer), session: AsyncSession = Depends(get_session)):
    candidates_enabled()
    row = await session.scalar(select(MemoryPreference).where(MemoryPreference.tenant_id == user.tenant_id, MemoryPreference.user_id == user.user_id))
    return {"data": {"auto_extract": row.auto_extract if row else False, "version": row.version if row else 0}}


@router.put("/memory-preferences")
async def update_preferences(payload: PreferenceUpdate, user: Principal = Depends(customer), session: AsyncSession = Depends(get_session)):
    candidates_enabled()
    row = await session.scalar(select(MemoryPreference).where(MemoryPreference.tenant_id == user.tenant_id, MemoryPreference.user_id == user.user_id))
    if row:
        result = await session.execute(update(MemoryPreference).where(MemoryPreference.id == row.id, MemoryPreference.version == payload.expected_version)
            .values(auto_extract=payload.auto_extract, version=payload.expected_version + 1))
        if result.rowcount != 1:
            raise HTTPException(409, "设置已更新")
    elif payload.expected_version == 0:
        session.add(MemoryPreference(tenant_id=user.tenant_id, user_id=user.user_id, auto_extract=payload.auto_extract))
    else:
        raise HTTPException(409, "设置版本不一致")
    await commit(session)
    return await preferences(user, session)


async def extract_candidates(session, row, messages):
    s = get_settings()
    if not s.chat_memory_candidates_enabled or not s.chat_memory_items_enabled:
        return
    await session.execute(update(MemoryCandidate).where(MemoryCandidate.tenant_id == row.tenant_id,
        MemoryCandidate.user_id == row.user_id, MemoryCandidate.status == "pending", MemoryCandidate.expires_at <= time.time())
        .values(status="expired", version=MemoryCandidate.version + 1))
    preference = await session.scalar(select(MemoryPreference).where(MemoryPreference.tenant_id == row.tenant_id, MemoryPreference.user_id == row.user_id))
    if not preference or not preference.auto_extract:
        return
    for message in messages:
        quote = message["text"].strip()
        # Deliberately allowlisted explicit self-description/preferences, no model inference.
        if not re.match(r"^(请记住[：:]?|以后请|我的偏好是|我是)", quote) or len(quote) > 1000:
            continue
        category = "background" if quote.startswith("我是") else "preference"
        key = "self_description" if category == "background" else "explicit_preference"
        fingerprint = digest([re.sub(r"\s+", " ", quote).casefold(), "workspace"])
        exists = await session.scalar(select(MemoryCandidate.id).where(MemoryCandidate.tenant_id == row.tenant_id,
            MemoryCandidate.user_id == row.user_id, MemoryCandidate.fingerprint == fingerprint))
        if not exists:
            session.add(MemoryCandidate(tenant_id=row.tenant_id, user_id=row.user_id, conversation_id=row.id,
                source_message_id=message["id"], fingerprint=fingerprint, content=quote, category=category, key=key,
                expires_at=time.time() + 30 * 86400))
            await session.flush()


@router.get("/memory-candidates")
async def candidates(user: Principal = Depends(customer), session: AsyncSession = Depends(get_session)):
    candidates_enabled()
    rows = list(await session.scalars(select(MemoryCandidate).where(MemoryCandidate.tenant_id == user.tenant_id,
        MemoryCandidate.user_id == user.user_id, MemoryCandidate.status == "pending")))
    result = []
    for row in rows:
        if row.expires_at <= time.time():
            continue
        try:
            await owned(session, row.conversation_id, user)
        except HTTPException:
            continue
        matches = list(await session.scalars(select(MemoryItem).where(visible(user), MemoryItem.scope == "workspace",
            MemoryItem.scope_id == user.tenant_id, MemoryItem.key == row.key)))
        result.append({"id": row.id, "content": row.content, "category": row.category, "key": row.key,
            "scope": "workspace", "version": row.version, "conversation_id": row.conversation_id,
            "source_message_id": row.source_message_id, "duplicates": [view(m) for m in matches if m.content == row.content],
            "conflicts": [view(m) for m in matches if m.content != row.content]})
    return {"data": result}


class CandidateDecision(BaseModel):
    expected_version: int = Field(ge=1)
    replace_id: UUID | None = None
    replace_version: int | None = None


@router.post("/memory-candidates/{identifier}/{decision}")
async def decide_candidate(identifier: UUID, decision: Literal["accept", "reject"], payload: CandidateDecision,
    user: Principal = Depends(customer), session: AsyncSession = Depends(get_session)):
    candidates_enabled()
    row = await session.scalar(select(MemoryCandidate).where(MemoryCandidate.id == str(identifier),
        MemoryCandidate.tenant_id == user.tenant_id, MemoryCandidate.user_id == user.user_id))
    if row is None:
        raise HTTPException(404, "候选不存在")
    conversation = await owned(session, row.conversation_id, user)
    source = next((m for i, m in enumerate(conversation.messages) if message_id(m, i) == row.source_message_id), None)
    if not source or source["question"].strip() != row.content or row.expires_at <= time.time():
        raise HTTPException(409, "候选来源已失效或过期")
    changed = await session.execute(update(MemoryCandidate).where(MemoryCandidate.id == row.id,
        MemoryCandidate.version == payload.expected_version, MemoryCandidate.status == "pending")
        .values(status="accepted" if decision == "accept" else "rejected", version=payload.expected_version + 1))
    if changed.rowcount != 1:
        raise HTTPException(409, "候选已处理，请刷新")
    if decision == "accept":
        existing = await session.scalar(select(MemoryItem).where(visible(user), MemoryItem.scope == "workspace",
            MemoryItem.scope_id == user.tenant_id, MemoryItem.key == row.key))
        source_info = {"kind": "candidate_confirmed", "conversation_id": row.conversation_id, "message_id": row.source_message_id}
        if existing:
            if existing.content != row.content and (str(payload.replace_id) != existing.id or payload.replace_version != existing.version):
                raise HTTPException(409, "存在冲突，必须明确确认替换并提供当前版本")
            result = await session.execute(update(MemoryItem).where(MemoryItem.id == existing.id, MemoryItem.version == existing.version)
                .values(content=row.content, source=source_info, status="active", version=existing.version + 1))
            if result.rowcount != 1:
                raise HTTPException(409, "记忆已更新")
            row.memory_item_id = existing.id
        else:
            item = MemoryItem(user_id=user.user_id, tenant_id=user.tenant_id, scope="workspace", scope_id=user.tenant_id,
                category=row.category, key=row.key, content=row.content, source=source_info)
            session.add(item)
            await session.flush()
            row.memory_item_id = item.id
    await commit(session)
    return {"data": {"status": row.status, "version": row.version, "memory_item_id": row.memory_item_id}}
