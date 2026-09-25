from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.access import (
    accessible_project_filter,
    accessible_project_or_404,
    project_access,
    require_project_permission,
)
from backend.audit import audit_event
from backend.config import get_settings
from backend.db import get_session
from backend.knowledge import index_document
from backend.models import KnowledgeDocument, Project, ProjectTask, ProjectTaskDocument, User
from backend.security import Principal, current_principal, require_company_admin

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


async def authorize_submission(session, project_id, user, task_id=None):
    task = None
    if project_id:
        project = await accessible_project_or_404(session, str(project_id), user)
        await require_project_permission(session, project, user, "project.document.submit")
        if user.session_context != "customer":
            raise HTTPException(403, "只允许项目成员提交")
        if project.lifecycle_status in {"completed", "archived", "cancelled"}:
            raise HTTPException(409, "项目已结束，文档以只读方式保留")
        if task_id:
            task = await session.scalar(
                select(ProjectTask).where(
                    ProjectTask.id == str(task_id), ProjectTask.project_id == project.id
                )
            )
            if not task:
                raise HTTPException(404, "任务不存在")
            if task.status in {"pending_review", "done"}:
                raise HTTPException(409, "审批中或已通过的任务不能追加文档")
    else:
        if task_id:
            raise HTTPException(422, "任务文档必须指定项目")
        await require_company_admin(user)
    return task


def visible_documents(user):
    visible = select(Project.id).where(
        accessible_project_filter(user), Project.deleted_at.is_(None)
    )
    return or_(
        (KnowledgeDocument.tenant_id == user.tenant_id) & KnowledgeDocument.project_id.is_(None),
        KnowledgeDocument.project_id.in_(visible),
    )


class RagInspectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=4000)
    project_id: UUID | None = None
    evidence_budget: Literal[600, 1200, 1800] = 1200


@router.post("/inspect")
async def inspect_rag(
    payload: RagInspectRequest,
    user: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
):
    from backend.rag_v3 import inspect, needs_retrieval

    question = payload.question.strip()
    if not question:
        raise HTTPException(422, "请输入问题")
    if payload.project_id:
        await accessible_project_or_404(session, str(payload.project_id), user)
        project_ids = [str(payload.project_id)]
    else:
        project_ids = list(
            await session.scalars(
                select(Project.id).where(
                    accessible_project_filter(user), Project.deleted_at.is_(None)
                )
            )
        )
    try:
        needed = needs_retrieval(question)
        result = (
            await inspect(session, user.tenant_id, question, project_ids, payload.evidence_budget)
            if needed
            else {"pipeline": "v3", "status": "not_needed", "chunks": [], "evidence_text": ""}
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "知识检索服务暂不可用，请稍后重试") from None
    return {
        "data": {
            "question": question,
            "needs_rag": needed,
            "reason": "不确定时检索；证据完整性单独标注"
            if needed
            else "明确问候或致谢，无需知识库检索",
            "decision_mode": "rules",
            "retrieval_mode": get_settings().rag_mode,
            **result,
        }
    }


class KnowledgeUpload(BaseModel):
    project_id: UUID | None = None
    task_id: UUID | None = None
    title: str = Field(min_length=2, max_length=160)
    version: int = Field(ge=1)
    module: str = Field(min_length=2, max_length=60)
    source: str = Field(min_length=3, max_length=500)
    license: str = Field(min_length=3, max_length=500)
    body: str = Field(min_length=20, max_length=30000)


@router.post("/files")
async def upload_file(
    request: Request,
    file: UploadFile,
    title: str = Form(min_length=2, max_length=160),
    version: int = Form(ge=1),
    module: str = Form(min_length=2, max_length=60),
    license: str = Form(min_length=3, max_length=500),
    project_id: UUID | None = Form(None),
    task_id: UUID | None = Form(None),
    csv_header: bool = Form(True),
    user: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
):
    from backend.models import Tenant
    from backend.rag_v3_binary import validate_binary
    from backend.rag_v3_index import register
    from backend.rag_v3_parse import max_bytes, validate_filename

    name = (file.filename or "").replace("\\", "/").split("/")[-1][:200]
    suffix = validate_filename(name)
    if suffix == ".pdf" and not get_settings().rag_pdf_enabled:
        raise HTTPException(415, "PDF 上传暂未开放")
    if suffix in {".pptx", ".xlsx"} and not get_settings().rag_office_enabled:
        raise HTTPException(415, "Office 文件上传暂未开放")
    limit = max_bytes(suffix)
    raw = await file.read(limit + 1)
    if len(raw) > limit:
        raise HTTPException(413, f"文件不能超过 {limit // (1024 * 1024)} MiB")
    if not raw:
        raise HTTPException(422, "文件为空")
    validate_binary(suffix, raw)
    scope = str(project_id) if project_id else None
    task = await authorize_submission(session, scope, user, task_id)
    await session.execute(select(Tenant.id).where(Tenant.id == user.tenant_id).with_for_update())
    previous = await session.scalar(
        select(KnowledgeDocument)
        .where(KnowledgeDocument.tenant_id == user.tenant_id, KnowledgeDocument.title == title)
        .order_by(KnowledgeDocument.version.desc())
    )
    if previous and (version <= previous.version or scope != previous.project_id):
        raise HTTPException(409, "版本必须递增，且同一标题须保持原项目范围")
    item = KnowledgeDocument(
        tenant_id=user.tenant_id,
        submitted_by=user.user_id,
        project_id=scope,
        title=title,
        version=version,
        module=module,
        source=name,
        license=license,
        body="",
        index_status="v3_pending",
    )
    session.add(item)
    await session.flush()
    if task:
        session.add(
            ProjectTaskDocument(
                tenant_id=task.tenant_id,
                project_id=task.project_id,
                task_id=task.id,
                document_id=item.id,
                submitted_by=user.user_id,
            )
        )
    source = await register(session, item, name, raw, {"csv_header": csv_header})
    session.add(
        audit_event(
            request,
            user,
            "knowledge.file_uploaded",
            item.id,
            {"filename": name, "version": version},
        )
    )
    await session.commit()
    return {
        "data": {
            "id": item.id,
            "document_id": item.id,
            "parse_status": source.phase,
            "index_status": source.phase,
            "indexing_enabled": get_settings().rag_v3_indexing_enabled,
            "warnings": [],
            "message": "等待异步解析和索引；图片、公式或 OCR 内容以文档详情中的解析告警为准",
        }
    }


@router.get("")
async def list_documents(
    user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)
):
    from backend.rag_v3_models import V3Document

    rows = (
        await session.scalars(
            select(KnowledgeDocument)
            .where(visible_documents(user))
            .order_by(KnowledgeDocument.created_at.desc())
        )
    ).all()
    indexed = {
        source.origin_id: source for source in await session.scalars(
            select(V3Document).where(V3Document.origin_id.in_([d.id for d in rows]))
        )
    } if rows else {}
    submitters = dict(
        (
            await session.execute(
                select(User.id, User.display_name).where(
                    User.id.in_({d.submitted_by for d in rows if d.submitted_by})
                )
            )
        ).all()
    )
    return {
        "data": [
            {
                "id": d.id,
                "project_id": d.project_id,
                "title": d.title,
                "version": d.version,
                "module": d.module,
                "source": d.source,
                "license": d.license,
                "active": d.active,
                "index_status": (f"v3_{indexed[d.id].phase}" if d.id in indexed
                                 else "v3_missing_original" if d.source.lower().endswith((".pdf", ".pptx", ".xlsx"))
                                 else "v3_unsupported" if d.source.lower().endswith((".doc", ".ppt", ".xls"))
                                 else "v3_pending"),
                "index_error": (indexed[d.id].error if d.id in indexed
                                else "原始文件未保存，请重新上传" if d.source.lower().endswith((".pdf", ".pptx", ".xlsx"))
                                else "当前文件格式不支持索引" if d.source.lower().endswith((".doc", ".ppt", ".xls"))
                                else ""),
                "index_version": d.index_version,
                "submitted_by": d.submitted_by,
                "submitter_name": submitters.get(d.submitted_by),
                "created_at": d.created_at,
            }
            for d in rows
        ]
    }


@router.post("")
async def upload_document(
    payload: KnowledgeUpload,
    request: Request,
    user: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
):
    task = await authorize_submission(session, payload.project_id, user, payload.task_id)
    # Tenant row serializes concurrent version publication.
    from backend.models import Tenant

    await session.execute(select(Tenant.id).where(Tenant.id == user.tenant_id).with_for_update())
    previous = await session.scalar(
        select(KnowledgeDocument)
        .where(
            KnowledgeDocument.tenant_id == user.tenant_id, KnowledgeDocument.title == payload.title
        )
        .order_by(KnowledgeDocument.version.desc())
    )
    if previous and payload.version <= previous.version:
        raise HTTPException(409, "知识版本必须递增")
    scope = str(payload.project_id) if payload.project_id else None
    if previous and previous.project_id != scope:
        raise HTTPException(
            409, "同一资料的新版本必须保持原项目范围；请使用不同标题发布其他范围的资料"
        )
    item = KnowledgeDocument(
        tenant_id=user.tenant_id,
        submitted_by=user.user_id,
        **payload.model_dump(exclude={"project_id", "task_id"}),
        project_id=scope,
    )
    session.add(item)
    await session.flush()
    if task:
        session.add(
            ProjectTaskDocument(
                tenant_id=task.tenant_id,
                project_id=task.project_id,
                task_id=task.id,
                document_id=item.id,
                submitted_by=user.user_id,
            )
        )
    from backend.rag_v3_index import register

    await register(session, item)
    if get_settings().execution_mode == "inline" and get_settings().rag_mode == "mock":
        await index_document(session, item)
    elif get_settings().rag_mode == "real":
        from backend.retrieval_sources import sync_knowledge

        await sync_knowledge(session, item)
    session.add(
        audit_event(
            request,
            user,
            "knowledge.uploaded",
            item.id,
            {"title": item.title, "version": item.version},
        )
    )
    await session.commit()
    return {"data": {"id": item.id, "index_status": item.index_status}}


@router.get("/{document_id}")
async def read_document(
    document_id: UUID,
    user: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
):
    item = await session.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == str(document_id), visible_documents(user)
        )
    )
    if item is None:
        raise HTTPException(404, "文档不存在或无权访问")
    from backend.rag_v3_models import V3Document

    v3 = await session.scalar(
        select(V3Document).where(
            V3Document.origin_id == item.id, V3Document.tenant_id == user.tenant_id
        )
    )
    can_reindex = False
    if v3 and v3.phase == "failed" and item.active and user.session_context == "customer":
        if item.project_id:
            project = await accessible_project_or_404(session, item.project_id, user)
            access = await project_access(session, project, user)
            can_reindex = "project.document.submit" in access["permissions"]
        else:
            can_reindex = user.company_role_code == "company_admin"
    return {
        "data": {
            "id": item.id,
            "project_id": item.project_id,
            "title": item.title,
            "body": item.body,
            "version": item.version,
            "module": item.module,
            "source": item.source,
            "license": item.license,
            "active": item.active,
            "can_reindex": can_reindex,
            "index_status": (f"v3_{v3.phase}" if v3 else
                             "v3_missing_original" if item.source.lower().endswith((".pdf", ".pptx", ".xlsx")) else
                             "v3_unsupported" if item.source.lower().endswith((".doc", ".ppt", ".xls")) else
                             item.index_status),
            "index_error": (v3.error if v3 else
                            "原始文件未保存，请重新上传" if item.source.lower().endswith((".pdf", ".pptx", ".xlsx")) else
                            item.index_error),
            "v3": {
                "phase": v3.phase,
                "error": v3.error,
                "warnings": v3.warnings,
                "filename": v3.filename,
                "metrics": v3.metrics,
            }
            if v3
            else None,
        }
    }


@router.post("/{document_id}/v3-index")
async def v3_index(
    document_id: UUID,
    request: Request,
    user: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
):
    from backend.rag_v3_index import register

    item = await session.scalar(
        select(KnowledgeDocument)
        .where(
            KnowledgeDocument.id == str(document_id),
            KnowledgeDocument.tenant_id == user.tenant_id,
            KnowledgeDocument.active.is_(True),
        )
        .with_for_update()
    )
    if not item:
        raise HTTPException(404, "资料不存在或已停用")
    if item.project_id:
        if user.session_context != "customer":
            raise HTTPException(403, "只允许项目成员重建索引")
        project = await accessible_project_or_404(session, item.project_id, user)
        await require_project_permission(session, project, user, "project.document.submit")
    else:
        await require_company_admin(user)
    if item.source.lower().endswith((".doc", ".ppt", ".xls")):
        raise HTTPException(415, "当前文件格式不支持索引，请转换后重新上传")
    if item.source.lower().endswith((".pdf", ".pptx", ".xlsx")):
        from backend.rag_v3_models import V3Document

        existing = await session.scalar(select(V3Document).where(V3Document.origin_id == item.id))
        if not existing:
            raise HTTPException(409, "原始文件未保存，请重新上传")
    source = await register(session, item)
    source.phase = "pending"
    source.attempts = 0
    source.error = ""
    if item.index_status.startswith("v3_"):
        item.index_status = "v3_pending"
        item.index_error = ""
    session.add(audit_event(request, user, "knowledge.reindex_requested", item.id,
                            {"project_id": item.project_id}))
    await session.commit()
    return {"data": {"id": item.id, "index_status": source.phase}}


@router.post("/{document_id}/deactivate")
async def deactivate(
    document_id: UUID,
    request: Request,
    user: Principal = Depends(require_company_admin),
    session: AsyncSession = Depends(get_session),
):
    item = await session.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == str(document_id), KnowledgeDocument.tenant_id == user.tenant_id
        )
    )
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
async def reindex(
    document_id: UUID,
    request: Request,
    user: Principal = Depends(require_company_admin),
    session: AsyncSession = Depends(get_session),
):
    item = await session.scalar(
        select(KnowledgeDocument)
        .where(
            KnowledgeDocument.id == str(document_id), KnowledgeDocument.tenant_id == user.tenant_id
        )
        .with_for_update()
    )
    if not item or not item.active:
        raise HTTPException(404, "资料不存在或已停用")
    if item.index_status.startswith("v3_"):
        raise HTTPException(409, "此文件属于 V3，请使用 V3 索引重建入口")
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
