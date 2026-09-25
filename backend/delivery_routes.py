from __future__ import annotations

import csv
import html
import io
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.access import accessible_project_or_404, require_project_permission
from backend.audit import audit_event
from backend.db import get_session
from backend.models import (
    AgentRun,
    ImportJob,
    ProjectArtifact,
    ProjectFeedback,
    SaaSMember,
    SaaSWorkspace,
    ToolExecution,
)
from backend.security import Principal, current_principal, idempotency_key

router = APIRouter(prefix="/api", tags=["delivery"])


async def authorized_project(session, project_id, user, permission):
    project = await accessible_project_or_404(session, str(project_id), user)
    await require_project_permission(session, project, user, permission)
    return project


@router.get("/projects/{project_id}/delivery")
async def delivery_details(project_id: UUID, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    project = await authorized_project(session, project_id, user, "artifact.view")
    artifacts = (await session.scalars(select(ProjectArtifact).where(ProjectArtifact.project_id == project.id).order_by(ProjectArtifact.created_at.desc()))).all()
    jobs = (await session.scalars(select(ImportJob).where(ImportJob.project_id == project.id))).all()
    ws = await session.scalar(select(SaaSWorkspace).where(SaaSWorkspace.project_id == project.id))
    members = (await session.scalars(select(SaaSMember).where(SaaSMember.workspace_id == ws.id))).all() if ws else []
    receipts = (await session.scalars(select(ToolExecution).where(ToolExecution.project_id == project.id))).all()
    feedback = (await session.scalars(select(ProjectFeedback).where(ProjectFeedback.project_id == project.id))).all()
    from backend.models import RemediationTask
    tasks = (await session.scalars(select(RemediationTask).where(RemediationTask.project_id == project.id))).all()
    return {"data": {
        "configuration": ws.configuration if ws else None,
        "remediation": [{"id": t.id, "run_id": t.run_id, "title": t.check_name, "status": t.status, "evidence": t.evidence} for t in tasks],
        "members": [{"name": m.name, "email": m.email, "department": m.department, "role": m.role} for m in members],
        "artifacts": [{"id": a.id, "run_id": a.run_id, "kind": a.kind, "title": a.title, "version": a.version, "checksum": a.checksum} for a in artifacts],
        "imports": [{"id": j.id, "run_id": j.run_id, "status": j.status, "validation": j.validation, "result": j.result} for j in jobs],
        "snapshots": [{"id": r.id, "run_id": r.run_id, "before": r.before, "result": r.result} for r in receipts],
        "feedback": [{"id": f.id, "run_id": f.run_id, "content": f.content, "author": f.author} for f in feedback],
    }}


@router.get("/artifacts/{artifact_id}")
async def download_artifact(artifact_id: UUID, preview: bool = False, format: Literal["markdown", "json"] = "markdown", user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    item = await session.get(ProjectArtifact, str(artifact_id))
    if item is None:
        raise HTTPException(404, "交付物不存在")
    await authorized_project(session, item.project_id, user, "artifact.view")
    from backend.storage import read
    content = await read(item)
    if format == "json":
        return JSONResponse({"id": item.id, "project_id": item.project_id, "run_id": item.run_id, "kind": item.kind, "title": item.title, "version": item.version, "checksum": item.checksum, "content": content}, headers={"Content-Disposition": f'attachment; filename="{item.kind}-v{item.version}.json"'})
    if preview:
        return HTMLResponse("<!doctype html><meta charset=utf-8><title>交付物预览</title><pre style='white-space:pre-wrap'>" + html.escape(content) + "</pre>", headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})
    return Response(content, media_type="text/markdown", headers={"Content-Disposition": f'attachment; filename="{item.kind}-v{item.version}.md"'})


@router.get("/imports/{job_id}")
async def import_details(job_id: UUID, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    item = await session.get(ImportJob, str(job_id))
    if item is None:
        raise HTTPException(404, "导入不存在")
    await authorized_project(session, item.project_id, user, "import.view")
    return {"data": {"id": item.id, "run_id": item.run_id, "status": item.status, "validation": item.validation, "result": item.result}}


@router.get("/imports/{job_id}/errors.csv")
async def import_errors(job_id: UUID, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    data = (await import_details(job_id, user, session))["data"]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["row", "field", "message", "suggestion"])
    errors = data["validation"].get("errors", []) + [r for r in data["result"].get("rows", []) if r["status"] == "failed"]
    for error in errors:
        writer.writerow([error["row"], error.get("field", "row"), error.get("message", error.get("error")), error.get("suggestion", "修正后通过新 Run 重新校验，已成功成员不会重复创建")])
    return Response("\ufeff" + output.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="import-errors.csv"'})


class FeedbackRequest(BaseModel):
    run_id: UUID
    content: str = Field(min_length=5, max_length=4000)


class FailedRowsRetry(BaseModel):
    csv_text: str | None = Field(default=None, max_length=2000000)


@router.post("/imports/{job_id}/retry-failed")
async def retry_failed_rows(job_id: UUID, payload: FailedRowsRetry, request: Request, key: str = Depends(idempotency_key), user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    import hashlib

    from sqlalchemy import func

    from backend import delivery
    from backend.config import get_settings
    from backend.execution import dispatch
    from backend.models import IdempotencyRecord
    from backend.schemas import ImplementationGraphState, RunStatus
    from backend.state_machine import (
        ProjectLifecycle,
        RunLifecycle,
        transition_project,
        transition_run,
    )
    job = await session.get(ImportJob, str(job_id))
    if job is None:
        raise HTTPException(404, "导入不存在")
    project = await authorized_project(session, job.project_id, user, "import.validate")
    await require_project_permission(session, project, user, "run.retry")
    await session.refresh(project, with_for_update=True)
    scope = f"retry_import:{job.id}"
    previous = await session.scalar(select(IdempotencyRecord).where(IdempotencyRecord.tenant_id == user.tenant_id, IdempotencyRecord.scope == scope, IdempotencyRecord.key == key))
    if previous:
        return {"data": previous.response}
    latest = await session.scalar(select(AgentRun).where(AgentRun.project_id == project.id).order_by(AgentRun.run_number.desc()))
    if project.lifecycle_status != "blocked" or not latest or latest.status not in {"failed", "cancelled"}:
        raise HTTPException(409, "必须先终止旧执行；重试创建新的审批 Run")
    failed = {row["row"] for row in job.result.get("rows", []) if row["status"] == "failed"}
    if not failed:
        raise HTTPException(409, "该导入没有失败行")
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["name", "email", "department", "role"])
    writer.writeheader()
    for number, row in enumerate(delivery.csv_rows(job), 2):
        if number in failed:
            writer.writerow(row)
    csv_text = payload.csv_text if payload.csv_text is not None else output.getvalue()
    if len(csv_text.encode()) > 2000000:
        raise HTTPException(422, "CSV 超过 2 MB")
    validation = await delivery.validate_csv(session, project, csv_text)
    if not validation["valid"] or validation["row_count"] != len(failed):
        raise HTTPException(422, {"message": "请仅修复原失败行，数量必须一致", "validation": validation})
    validation["retry_of_job_id"] = job.id
    run = AgentRun(tenant_id=project.tenant_id, project_id=project.id, started_by=user.user_id, run_number=(await session.scalar(select(func.max(AgentRun.run_number)).where(AgentRun.project_id == project.id)) or 0) + 1, retry_of_run_id=latest.id, trace_id=request.state.trace_id)
    session.add(run)
    await session.flush()
    new_job = ImportJob(tenant_id=project.tenant_id, project_id=project.id, run_id=run.id, status="valid", csv_text=csv_text, source_hash=hashlib.sha256(csv_text.encode()).hexdigest(), validation=validation)
    session.add(new_job)
    await session.flush()
    transition_project(project, ProjectLifecycle.IN_PROGRESS)
    if get_settings().execution_mode == "inline":
        transition_run(run, RunLifecycle.RUNNING)
    run.state = ImplementationGraphState(project_id=UUID(project.id), run_id=UUID(run.id), status=RunStatus(run.status), import_job_id=UUID(new_job.id), engine_version=get_settings().agent_engine).model_dump(mode="json")
    await dispatch(session, run)
    data = {"run_id": run.id, "job_id": new_job.id}
    session.add(IdempotencyRecord(tenant_id=user.tenant_id, scope=scope, key=key, response=data))
    session.add(audit_event(request, user, "import.retry_requested", new_job.id, {"original_job": job.id, "run_id": run.id}))
    await session.commit()
    return {"data": data}


class MaterialRevision(BaseModel):
    expected_version: int = Field(ge=1)
    requirements_text: str = Field(min_length=20, max_length=10000)
    migration_scope: str = Field(max_length=2000)
    acceptance_criteria: str = Field(max_length=2000)


from backend.schemas import ProjectCreate


class ProjectRevision(ProjectCreate):
    expected_version: int = Field(ge=1)


@router.patch("/projects/{project_id}")
async def revise_project(project_id: UUID, payload: ProjectRevision, request: Request, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    from backend.config import get_settings
    from backend.models import ProjectDocument
    project = await authorized_project(session, project_id, user, "project.edit")
    await session.refresh(project, with_for_update=True)
    if project.lifecycle_status in {"completed", "archived"}:
        raise HTTPException(409, "已完成或已归档项目为只读")
    if project.version != payload.expected_version:
        raise HTTPException(409, "项目已被更新，请刷新后重试")
    active = await session.scalar(select(AgentRun.id).where(AgentRun.project_id == project.id,
        AgentRun.status.in_(["pending", "running", "preparing_materials", "waiting_approval", "blocked"])).limit(1))
    if active:
        raise HTTPException(409, "请先取消当前执行，再保存修改并重新发起审批")
    doc = await session.scalar(select(ProjectDocument).where(ProjectDocument.project_id == project.id))
    if doc is None:
        raise HTTPException(409, "项目文书缺失")
    before = dict(doc.content)
    # Company ownership and collaborations have their own permission-controlled APIs.
    values = payload.model_dump(mode="json", exclude={"expected_version", "company_id", "assisting_company_id", "customer_name"})
    project.name = payload.name
    project.requirements_text = payload.requirements_text
    project.version += 1
    doc.content = {**doc.content, **values, "customer_name": project.customer_name}
    if get_settings().rag_mode == "real":
        from backend.retrieval_sources import sync_project
        await sync_project(session, project)
    session.add(audit_event(request, user, "project.details_revised", project.id, {"before": before, "after": doc.content}))
    await session.commit()
    return {"data": {"id": project.id, "version": project.version}}


@router.patch("/projects/{project_id}/materials")
async def revise_materials(project_id: UUID, payload: MaterialRevision, request: Request, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    from backend.models import ProjectDocument
    project = await authorized_project(session, project_id, user, "project.edit")
    await session.refresh(project, with_for_update=True)
    if project.lifecycle_status not in {"draft", "ready", "blocked"} or project.version != payload.expected_version:
        raise HTTPException(409, "仅能修订未启动或已阻塞项目，请刷新项目版本")
    active_agent = await session.scalar(select(AgentRun).where(AgentRun.project_id == project.id, AgentRun.status == "blocked").limit(1))
    if active_agent and active_agent.state.get("engine_version") == "v2":
        raise HTTPException(409, "修改已审批流程的原始需求需先取消当前 Run，再修订材料并创建新 Run 重新审批")
    if len(payload.requirements_text.strip()) < 20:
        raise HTTPException(422, "具体需求至少 20 字")
    doc = await session.scalar(select(ProjectDocument).where(ProjectDocument.project_id == project.id))
    if doc is None:
        raise HTTPException(409, "项目文书缺失，请联系管理员")
    before = {"requirements_text": project.requirements_text, "document": doc.content}
    project.requirements_text = payload.requirements_text.strip()
    project.version += 1
    doc.content = {**doc.content, **payload.model_dump(exclude={"expected_version"}), "requirements_text": project.requirements_text}
    from backend.config import get_settings
    if get_settings().rag_mode == "real":
        from backend.retrieval_sources import sync_project
        await sync_project(session, project)
    session.add(audit_event(request, user, "project.materials_revised", project.id, {"before": before, "after": doc.content}))
    await session.commit()
    return {"data": {"id": project.id, "version": project.version}}


class ConfigurationProposal(BaseModel):
    expected_version: int = Field(ge=1)
    departments: list[str] = Field(min_length=1, max_length=50)
    statuses: list[str] = Field(min_length=2, max_length=50)
    templates: list[str] = Field(min_length=1, max_length=50)
    custom_fields: list[str] = Field(max_length=50)
    due_date_notifications: bool


@router.patch("/runs/{run_id}/configuration")
async def revise_configuration(run_id: UUID, payload: ConfigurationProposal, request: Request, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    from backend import delivery
    from backend.models import Approval
    from backend.schemas import ConfigurationChange, ImplementationGraphState
    run = await session.scalar(select(AgentRun).where(AgentRun.id == str(run_id)).with_for_update())
    if not run:
        raise HTTPException(404, "Run 不存在")
    project = await authorized_project(session, run.project_id, user, "artifact.create")
    if run.status != "waiting_approval" or run.current_node != "configuration_approval" or run.version != payload.expected_version:
        raise HTTPException(409, "仅能修改当前待审批的配置方案，请刷新版本")
    ws = await delivery.workspace(session, project)
    values = {"departments": payload.departments, "workflow.statuses": payload.statuses, "templates": payload.templates, "custom_fields": payload.custom_fields, "notifications.due_date": payload.due_date_notifications}
    delivery.validate_configuration({**ws.configuration, **values})
    state = ImplementationGraphState.model_validate(run.state)
    old_approval = await session.get(Approval, str(state.pending_approval_id))
    if old_approval is None or old_approval.status != "pending":
        raise HTTPException(409, "审批已处理")
    old_approval.status = "cancelled"
    old_approval.version += 1
    state.configuration_changes = [ConfigurationChange(path=path, old_value=ws.configuration.get(path), new_value=value, risk="high", reason="实施顾问修订方案") for path, value in values.items() if ws.configuration.get(path) != value]
    item = Approval(tenant_id=run.tenant_id, run_id=run.id, kind="configuration", requested_by=user.user_id, payload=await delivery.material(session, project, run, state, "configuration"))
    session.add(item)
    await session.flush()
    state.pending_approval_id = UUID(item.id)
    run.state = state.model_dump(mode="json")
    run.version += 1
    session.add(audit_event(request, user, "configuration.revised", run.id, {"superseded_approval": old_approval.id, "approval_id": item.id}))
    await session.commit()
    return {"data": {"approval_id": item.id, "version": run.version}}


@router.post("/projects/{project_id}/feedback")
async def submit_feedback(project_id: UUID, payload: FeedbackRequest, request: Request, key: str = Depends(idempotency_key), user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    from backend.models import IdempotencyRecord

    project = await authorized_project(session, project_id, user, "acceptance.submit")
    scope = f"feedback:{project.id}:{user.user_id}"
    existing = await session.scalar(select(IdempotencyRecord).where(IdempotencyRecord.tenant_id == user.tenant_id, IdempotencyRecord.scope == scope, IdempotencyRecord.key == key))
    if existing:
        return {"data": existing.response}
    run = await session.get(AgentRun, str(payload.run_id))
    if not run or run.project_id != project.id:
        raise HTTPException(404, "Run 不存在")
    item = ProjectFeedback(tenant_id=project.tenant_id, project_id=project.id, run_id=run.id, author=user.user_id, content=payload.content)
    session.add(item)
    await session.flush()
    data = {"id": item.id}
    session.add(IdempotencyRecord(tenant_id=user.tenant_id, scope=scope, key=key, response=data))
    session.add(audit_event(request, user, "acceptance.feedback", item.id, {"run_id": run.id}))
    await session.commit()
    return {"data": data}
