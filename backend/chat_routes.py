from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.access import accessible_project_or_404, require_project_permission
from backend.chat import (
    MAX_FILE_BYTES,
    answer_question,
    evidence,
    extract_document,
    retrieval_summary,
)
from backend.chat_models import ChatConversation, ChatMemory
from backend.config import get_settings
from backend.db import get_session
from backend.models import uid
from backend.platform_knowledge import profile
from backend.rate_limit import enforce_rate_limit
from backend.security import Principal, current_principal

router = APIRouter(prefix="/api/chat", tags=["chat"])


async def customer(user: Principal = Depends(current_principal)) -> Principal:
    if user.account_type != "customer" or not user.tenant_id:
        raise HTTPException(403, "请使用客户空间访问 AI 助手")
    return user


async def owned(session, identifier, user):
    row = await session.scalar(select(ChatConversation).where(ChatConversation.id == str(identifier), ChatConversation.tenant_id == user.tenant_id, ChatConversation.user_id == user.user_id))
    if row is None:
        raise HTTPException(404, "对话不存在")
    if row.project_id:
        project = await accessible_project_or_404(session, row.project_id, user)
        await require_project_permission(session, project, user, "project.view")
    return row


def detail(row):
    return {"id": row.id, "title": row.title, "project_id": row.project_id, "version": row.version, "archived": row.archived, "pinned": row.pinned, "unread": row.unread,
            "messages": [dict(m, answer=retrieval_summary(m['question'], m['citations'])) if m.get('mode') == 'retrieval' else m for m in row.messages],
            "documents": [{"id": d["id"], "name": d["name"], "characters": len(d["text"])} for d in row.documents]}


async def save(session, row, expected_version, **values):
    result = await session.execute(update(ChatConversation).where(ChatConversation.id == row.id, ChatConversation.version == expected_version).values(**values, version=expected_version + 1))
    if result.rowcount != 1:
        raise HTTPException(409, "对话已在其他窗口更新，请刷新后重试")
    if "documents" in values and get_settings().rag_mode == "real":
        from backend.retrieval_sources import sync_conversation
        await sync_conversation(session, row, documents=values["documents"])
    await session.commit()
    await session.refresh(row)
    return {"data": detail(row)}


class ConversationCreate(BaseModel):
    project_id: UUID | None = None


class ConversationUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    archived: bool | None = None
    pinned: bool | None = None
    unread: bool | None = None


def require_unarchived(row):
    if row.archived:
        raise HTTPException(409, "对话已归档，请先恢复再继续提问或修改附件")


class MessageCreate(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    expected_version: int = Field(ge=1)
    request_id: UUID
    use_memory: bool = True
    stream: bool = False


class MemoryUpdate(BaseModel):
    content: str = Field(max_length=4000)
    expected_version: int = Field(ge=0)


@router.get("/conversations")
async def conversations(user: Principal = Depends(customer), session: AsyncSession = Depends(get_session), q: str = Query(default="", max_length=200), archived: bool = False):
    rows = (await session.scalars(select(ChatConversation).where(ChatConversation.tenant_id == user.tenant_id, ChatConversation.user_id == user.user_id, ChatConversation.archived == archived))).all()
    visible = []
    for row in rows:
        try:
            await owned(session, row.id, user)
            matches = [m for m in row.messages if q.strip().casefold() in (m['question'] + ' ' + m['answer']).casefold()]
            if q.strip() and q.strip().casefold() not in row.title.casefold() and not matches:
                continue
            last = row.messages[-1] if row.messages else {}
            preview = matches[-1]['question'] if q.strip() and matches else last.get('question', '')
            visible.append({"id": row.id, "title": row.title, "project_id": row.project_id, "archived": row.archived, "pinned": row.pinned, "unread": row.unread, "version": row.version,
                            "message_count": len(row.messages), "preview": preview[:160], "updated_at": last.get('created_at')})
        except HTTPException as exc:
            if exc.status_code not in {403, 404}:
                raise
    visible.sort(key=lambda item: item['updated_at'] or '', reverse=True)
    return {"data": {"conversations": visible, "mode": "llm" if get_settings().model_mode == "real" else "retrieval", "assistant": profile()}}


@router.get("/assistant")
async def assistant_description(user: Principal = Depends(customer)):
    return {"data": profile()}


@router.post("/conversations")
async def create(payload: ConversationCreate, user: Principal = Depends(customer), session: AsyncSession = Depends(get_session)):
    if payload.project_id:
        project = await accessible_project_or_404(session, str(payload.project_id), user)
        await require_project_permission(session, project, user, "project.view")
    row = ChatConversation(tenant_id=user.tenant_id, user_id=user.user_id, project_id=str(payload.project_id) if payload.project_id else None)
    session.add(row)
    await session.commit()
    return {"data": detail(row)}


@router.get("/memory")
async def memory(user: Principal = Depends(customer), session: AsyncSession = Depends(get_session)):
    row = await session.scalar(select(ChatMemory).where(ChatMemory.tenant_id == user.tenant_id, ChatMemory.user_id == user.user_id))
    return {"data": {"content": row.content if row else "", "version": row.version if row else 0}}


@router.put("/memory")
async def update_memory(payload: MemoryUpdate, user: Principal = Depends(customer), session: AsyncSession = Depends(get_session)):
    row = await session.scalar(select(ChatMemory).where(ChatMemory.tenant_id == user.tenant_id, ChatMemory.user_id == user.user_id))
    if row:
        result = await session.execute(update(ChatMemory).where(ChatMemory.id == row.id, ChatMemory.version == payload.expected_version).values(content=payload.content.strip(), version=row.version + 1).returning(ChatMemory.id))
        if result.scalar_one_or_none() is None:
            raise HTTPException(409, "记忆已更新，请刷新后重试")
    else:
        if payload.expected_version != 0:
            raise HTTPException(409, "记忆版本不一致")
        session.add(ChatMemory(tenant_id=user.tenant_id, user_id=user.user_id, content=payload.content.strip()))
    await session.commit()
    return await memory(user, session)


@router.get("/conversations/{identifier}")
async def get_conversation(identifier: UUID, user: Principal = Depends(customer), session: AsyncSession = Depends(get_session)):
    return {"data": detail(await owned(session, identifier, user))}


@router.patch("/conversations/{identifier}")
async def update_conversation(identifier: UUID, payload: ConversationUpdate, user: Principal = Depends(customer), session: AsyncSession = Depends(get_session)):
    row = await owned(session, identifier, user)
    changes: dict[str, str | bool] = {}
    if payload.title is not None:
        title = payload.title.strip()
        if not title:
            raise HTTPException(422, "对话名称不能为空")
        changes['title'] = title
    if payload.archived is not None:
        changes['archived'] = payload.archived
    for key in ('pinned', 'unread'):
        if getattr(payload, key) is not None:
            changes[key] = getattr(payload, key)
    if not changes:
        raise HTTPException(422, "请提供名称或归档状态")
    return await save(session, row, payload.expected_version, **changes)


@router.delete("/conversations/{identifier}")
async def delete_conversation(identifier: UUID, user: Principal = Depends(customer), session: AsyncSession = Depends(get_session)):
    # Deletion remains possible after losing project access; only ownership is needed.
    row = await session.scalar(select(ChatConversation).where(ChatConversation.id == str(identifier), ChatConversation.tenant_id == user.tenant_id, ChatConversation.user_id == user.user_id))
    if row is None:
        raise HTTPException(404, "对话不存在")
    if get_settings().rag_mode == "real":
        from backend.retrieval_sources import sync_conversation
        await sync_conversation(session, row, documents=[])
    await session.delete(row)
    await session.commit()
    return {"data": {"deleted": True}}


@router.post("/conversations/{identifier}/documents")
async def upload(identifier: UUID, file: UploadFile, expected_version: int, user: Principal = Depends(customer), session: AsyncSession = Depends(get_session)):
    row = await owned(session, identifier, user)
    require_unarchived(row)
    if len(row.documents) >= 10:
        raise HTTPException(422, "每个对话最多上传 10 份文档")
    raw = await file.read(MAX_FILE_BYTES + 1)
    name = (file.filename or "document.txt").replace("\\", "/").split("/")[-1][:160]
    from backend.rag_v3_parse import parse
    parsed = await asyncio.to_thread(parse, name, raw)
    text = '\n\n'.join((node.context+'\n' if node.context else '')+node.text for node in parsed.nodes if node.text)
    return await save(session, row, expected_version, documents=[*row.documents, {"id": uid(), "name": name, "text": text, 'warnings':parsed.warnings}])


@router.delete("/conversations/{identifier}/documents/{document_id}")
async def delete_document(identifier: UUID, document_id: UUID, expected_version: int, user: Principal = Depends(customer), session: AsyncSession = Depends(get_session)):
    row = await owned(session, identifier, user)
    require_unarchived(row)
    documents = [d for d in row.documents if d["id"] != str(document_id)]
    if len(documents) == len(row.documents):
        raise HTTPException(404, "文档不存在")
    return await save(session, row, expected_version, documents=documents)


@router.post("/conversations/{identifier}/messages")
async def send(identifier: UUID, payload: MessageCreate, request: Request, user: Principal = Depends(customer), session: AsyncSession = Depends(get_session)):
    row = await owned(session, identifier, user)
    for message in row.messages:
        if message["request_id"] == str(payload.request_id):
            if message["question"] != payload.question.strip():
                raise HTTPException(409, "请求标识已用于另一个问题")
            if payload.stream:
                from backend.chat_stream import event
                return StreamingResponse(iter([event("done", {"data": detail(row)})]), media_type="text/event-stream")
            return {"data": detail(row)}
    if row.version != payload.expected_version:
        raise HTTPException(409, "对话已更新，请刷新后重试")
    require_unarchived(row)
    if len(row.messages) >= 200:
        raise HTTPException(422, "当前对话已达到 200 轮，请新建对话；长期记忆会保留")
    question = payload.question.strip()
    if not question:
        raise HTTPException(422, "请输入问题")
    await enforce_rate_limit(f"chat:{user.tenant_id}:{user.user_id}", 20, 60)
    memo = await memory(user, session)
    if payload.stream:
        from backend.chat_stream import generate
        return StreamingResponse(generate(session, row, user, payload, request.app.openapi(),
            memo["data"]["content"] if payload.use_memory else ""), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    # Include recent user wording so follow-up pronouns can retrieve the same topic.
    query = question
    if row.messages and question.startswith(("那", "它", "这个", "这些", "继续", "还有", "为什么", "如何修复")) and not re.search(r"[A-Za-z]+-\d+", question):
        query += " " + row.messages[-1]['question'][-500:]
    try:
        sources = await evidence(session, row, user, query, request.app.openapi())
        answer = await answer_question(question, row.messages, memo["data"]["content"] if payload.use_memory else "", sources)
    except (httpx.HTTPError, TimeoutError):
        raise HTTPException(503, "模型或检索服务暂不可用，请稍后重试；本次问题未保存") from None
    message = {"request_id": str(payload.request_id), "question": question, "created_at": datetime.now(UTC).isoformat(), **answer}
    return await save(session, row, payload.expected_version, messages=[*row.messages, message], title=row.title if row.messages or row.title != '新对话' else question[:120])
