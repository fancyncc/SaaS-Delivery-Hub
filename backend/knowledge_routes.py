from __future__ import annotations

from uuid import UUID
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, Form
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.access import accessible_project_filter, accessible_project_or_404
from backend.audit import audit_event
from backend.config import get_settings
from backend.db import get_session
from backend.knowledge import index_document, retrieve  # noqa: F401 -- compatibility export
from backend.models import KnowledgeDocument, Project
from backend.security import Principal, current_principal, require_company_admin

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class RagInspectRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    project_id: UUID | None = None
    evidence_budget: Literal[600, 1200, 1800] = 1200
    pipeline: Literal['legacy', 'v2', 'v3'] | None = None


@router.post("/inspect")
async def inspect_rag(payload: RagInspectRequest, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    from backend.rag_inspection import decide_retrieval

    question = payload.question.strip()
    if not question:
        raise HTTPException(422, "请输入问题")
    if payload.project_id:
        await accessible_project_or_404(session, str(payload.project_id), user)
        project_ids = [str(payload.project_id)]
    else:
        project_ids = list(await session.scalars(select(Project.id).where(
            accessible_project_filter(user), Project.deleted_at.is_(None))))
    try:
        if payload.pipeline == 'v3':
            if not get_settings().rag_inspection_v3:
                raise HTTPException(503, 'V3 尚未启用：需先完成索引、模型对比、校准和锁定验证')
            from backend.rag_v3 import inspect, needs_retrieval
            needed = needs_retrieval(question)
            result = await inspect(session, user.tenant_id, question, project_ids, payload.evidence_budget) if needed else {
                'pipeline':'v3', 'status':'not_needed', 'chunks':[], 'evidence_text':''}
            return {'data': {'question':question, 'needs_rag':needed,
                'reason':'不确定时检索；证据完整性单独标注' if needed else '明确问候或致谢，无需知识库检索',
                'decision_mode':'rules', 'retrieval_mode':get_settings().rag_mode, **result}}
        if payload.pipeline == 'v2' and not get_settings().rag_inspection_v2:
            raise HTTPException(503, 'V2 未启用')
        decision = await decide_retrieval(question)
        if decision.needs_rag and get_settings().rag_inspection_v2 and payload.pipeline != 'legacy':
            from backend.inspection_evidence import inspect
            result = await inspect(session, user.tenant_id, question, project_ids, payload.evidence_budget)
            return {'data': {'question':question, **decision.model_dump(), **result,
                'decision_mode':'rules', 'retrieval_mode':get_settings().rag_mode}}
        chunks = await retrieve(session, user.tenant_id, question, project_ids=project_ids, knowledge_only=True) if decision.needs_rag else []
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "知识检索服务暂不可用，请稍后重试") from None
    return {"data": {"question": question, **decision.model_dump(), "chunks": chunks,
        "status": "not_needed" if not decision.needs_rag else "found" if chunks else "not_found",
        "decision_mode": "rules",
        "retrieval_mode": get_settings().rag_mode}}


class KnowledgeUpload(BaseModel):
    project_id: UUID | None = None
    title: str = Field(min_length=2, max_length=160)
    version: int = Field(ge=1)
    module: str = Field(min_length=2, max_length=60)
    source: str = Field(min_length=3, max_length=500)
    license: str = Field(min_length=3, max_length=500)
    body: str = Field(min_length=20, max_length=30000)


@router.post('/files')
async def upload_file(request: Request, file: UploadFile, title: str = Form(min_length=2,max_length=160),
    version: int = Form(ge=1), module: str = Form(min_length=2,max_length=60),
    license: str = Form(min_length=3,max_length=500), project_id: UUID | None = Form(None),
    csv_header: bool = Form(True), user: Principal = Depends(require_company_admin), session: AsyncSession = Depends(get_session)):
    from backend.rag_v3_parse import validate_filename, MAX_BYTES
    from backend.rag_v3_index import register
    from backend.models import Tenant
    name=(file.filename or '').replace('\\','/').split('/')[-1][:200]
    validate_filename(name)
    raw=await file.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES: raise HTTPException(413,'文件不能超过 2 MiB')
    if not raw: raise HTTPException(422,'文件为空')
    scope=str(project_id) if project_id else None
    if scope: await accessible_project_or_404(session,scope,user)
    await session.execute(select(Tenant.id).where(Tenant.id==user.tenant_id).with_for_update())
    previous=await session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.tenant_id==user.tenant_id,
        KnowledgeDocument.title==title).order_by(KnowledgeDocument.version.desc()))
    if previous and (version<=previous.version or scope!=previous.project_id):
        raise HTTPException(409,'版本必须递增，且同一标题须保持原项目范围')
    item=KnowledgeDocument(tenant_id=user.tenant_id,project_id=scope,title=title,version=version,
        module=module,source=name,license=license,body='',index_status='v3_pending')
    session.add(item); await session.flush()
    source=await register(session,item,name,raw,{'csv_header':csv_header})
    session.add(audit_event(request,user,'knowledge.file_uploaded',item.id,{'filename':name,'version':version}))
    await session.commit()
    return {'data': {'id':item.id,'document_id':item.id,'parse_status':source.phase,
        'index_status':source.phase,'indexing_enabled':get_settings().rag_v3_indexing_enabled,
        'warnings':[], 'message':'等待异步解析和独立索引；图片和公式不在解析范围内'}}


@router.get("")
async def list_documents(user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    visible = select(Project.id).where(accessible_project_filter(user), Project.deleted_at.is_(None))
    rows = (await session.scalars(select(KnowledgeDocument).where(KnowledgeDocument.tenant_id == user.tenant_id,
        or_(KnowledgeDocument.project_id.is_(None), KnowledgeDocument.project_id.in_(visible))).order_by(KnowledgeDocument.created_at.desc()))).all()
    return {"data": [{"id": d.id, "project_id": d.project_id, "title": d.title, "version": d.version, "module": d.module, "source": d.source, "license": d.license, "active": d.active, "index_status": d.index_status, "index_error": d.index_error, "index_version": d.index_version} for d in rows]}


@router.post("")
async def upload_document(payload: KnowledgeUpload, request: Request, user: Principal = Depends(require_company_admin), session: AsyncSession = Depends(get_session)):
    if payload.project_id:
        await accessible_project_or_404(session, str(payload.project_id), user)
    # Tenant row serializes concurrent version publication.
    from backend.models import Tenant
    await session.execute(select(Tenant.id).where(Tenant.id == user.tenant_id).with_for_update())
    previous = await session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.tenant_id == user.tenant_id, KnowledgeDocument.title == payload.title).order_by(KnowledgeDocument.version.desc()))
    if previous and payload.version <= previous.version:
        raise HTTPException(409, "知识版本必须递增")
    scope = str(payload.project_id) if payload.project_id else None
    if previous and previous.project_id != scope:
        raise HTTPException(409, "同一资料的新版本必须保持原项目范围；请使用不同标题发布其他范围的资料")
    item = KnowledgeDocument(tenant_id=user.tenant_id, **payload.model_dump(exclude={"project_id"}), project_id=scope)
    session.add(item)
    await session.flush()
    from backend.rag_v3_index import register
    await register(session, item)
    if get_settings().execution_mode == "inline" and get_settings().rag_mode == "mock":
        await index_document(session, item)
    elif get_settings().rag_mode == "real":
        from backend.retrieval_sources import sync_knowledge
        await sync_knowledge(session, item)
    session.add(audit_event(request, user, "knowledge.uploaded", item.id, {"title": item.title, "version": item.version}))
    await session.commit()
    return {"data": {"id": item.id, "index_status": item.index_status}}


@router.get("/{document_id}")
async def read_document(document_id: UUID, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    visible = select(Project.id).where(accessible_project_filter(user), Project.deleted_at.is_(None))
    item = await session.scalar(select(KnowledgeDocument).where(
        KnowledgeDocument.id == str(document_id), KnowledgeDocument.tenant_id == user.tenant_id,
        or_(KnowledgeDocument.project_id.is_(None), KnowledgeDocument.project_id.in_(visible))))
    if item is None:
        raise HTTPException(404, "文档不存在或无权访问")
    from backend.rag_v3_models import V3Document
    v3=await session.scalar(select(V3Document).where(V3Document.origin_id==item.id,V3Document.tenant_id==user.tenant_id))
    return {"data": {"id": item.id, "project_id": item.project_id, "title": item.title,
        "body": item.body, "version": item.version, "module": item.module, "source": item.source,
        "license": item.license, "active": item.active, "index_status": item.index_status,
        'v3': {'phase':v3.phase,'error':v3.error,'warnings':v3.warnings,'filename':v3.filename,'metrics':v3.metrics} if v3 else None}}


@router.post('/{document_id}/v3-index')
async def v3_index(document_id: UUID, user: Principal = Depends(require_company_admin), session: AsyncSession = Depends(get_session)):
    from backend.rag_v3_index import register
    item=await session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.id==str(document_id),
        KnowledgeDocument.tenant_id==user.tenant_id,KnowledgeDocument.active.is_(True)).with_for_update())
    if not item: raise HTTPException(404,'资料不存在或已停用')
    if item.project_id: await accessible_project_or_404(session,item.project_id,user)
    if item.source.lower().endswith(('.pdf','.doc')): raise HTTPException(415,'历史 PDF / DOC 不纳入 V3 迁移')
    source=await register(session,item)
    source.phase='pending'; source.attempts=0; source.error=''
    if item.index_status.startswith('v3_'): item.index_status='v3_pending'; item.index_error=''
    await session.commit()
    return {'data':{'id':item.id,'index_status':source.phase}}


@router.post("/{document_id}/deactivate")
async def deactivate(document_id: UUID, request: Request, user: Principal = Depends(require_company_admin), session: AsyncSession = Depends(get_session)):
    item = await session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.id == str(document_id), KnowledgeDocument.tenant_id == user.tenant_id))
    if not item:
        raise HTTPException(404, "资料不存在")
    item.active = False
    if get_settings().rag_mode == "real":
        from backend.retrieval_sources import sync_knowledge
        await sync_knowledge(session, item)
    session.add(audit_event(request, user, "knowledge.deactivated", item.id, {}))
    await session.commit()
    return {"data": {"id": item.id, "active": False}}


@router.post("/{document_id}/reindex")
async def reindex(document_id: UUID, request: Request, user: Principal = Depends(require_company_admin), session: AsyncSession = Depends(get_session)):
    item = await session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.id == str(document_id), KnowledgeDocument.tenant_id == user.tenant_id).with_for_update())
    if not item or not item.active:
        raise HTTPException(404, "资料不存在或已停用")
    if item.index_status.startswith('v3_'):
        raise HTTPException(409, '此文件属于 V3，请使用 V3 索引重建入口')
    item.index_status, item.index_attempts, item.index_error = "pending", 0, ""
    if get_settings().rag_mode == "real":
        from backend.retrieval_sources import sync_knowledge
        source = await sync_knowledge(session, item)
        source.phase, source.attempts, source.error_code = "pending", 0, ""
        source.generation += 1
    if get_settings().execution_mode == "inline" and get_settings().rag_mode == "mock":
        await index_document(session, item)
    session.add(audit_event(request, user, "knowledge.reindexed", item.id, {}))
    await session.commit()
    return {"data": {"id": item.id, "index_status": item.index_status}}
