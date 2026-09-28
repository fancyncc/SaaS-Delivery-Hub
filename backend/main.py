from __future__ import annotations

import asyncio
import hashlib
import json
import math
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend import delivery
from backend.access import (
    accessible_project_filter,
    accessible_project_or_404,
    project_access,
    require_project_permission,
)
from backend.admin_routes import router as company_router
from backend.audit import audit_event
from backend.auth_routes import router as auth_router
from backend.chat_routes import router as chat_router
from backend.config import get_settings
from backend.db import bootstrap_identity, get_session, init_db
from backend.delivery_routes import router as delivery_router
from backend.execution import dispatch
from backend.knowledge_routes import router as knowledge_router
from backend.member_import_routes import router as member_import_router
from backend.models import (
    AgentRun,
    AgentStep,
    Approval,
    IdempotencyRecord,
    ImportJob,
    Project,
    ProjectCollaboration,
    ProjectDocument,
    ProjectMembership,
    Tenant,
)
from backend.onboarding import router as onboarding_router
from backend.platform_routes import router as platform_router
from backend.project_access_routes import router as project_access_router
from backend.project_task_routes import router as project_task_router
from backend.retrieval_status import router as retrieval_status_router
from backend.schemas import (
    ApprovalDecision,
    Envelope,
    ImplementationGraphState,
    ImportValidateRequest,
    ProjectCreate,
    RunStatus,
)
from backend.security import (
    CSRF_COOKIE,
    Principal,
    current_principal,
    idempotency_key,
    require_company_admin,
)
from backend.state_machine import (
    ProjectLifecycle,
    RunLifecycle,
    transition_project,
    transition_run,
)
from backend.support_routes import platform_router as platform_support_router
from backend.support_routes import router as support_router
from backend.workflow import resume_after_approval


@asynccontextmanager
async def lifespan(_: FastAPI):
    from backend.observability import setup
    setup()
    await init_db()
    await bootstrap_identity()
    task = None
    if get_settings().local_indexer_enabled and get_settings().rag_mode == "real":
        from backend.local_indexer import maintain_indexes
        task = asyncio.create_task(maintain_indexes())
    context_task = None
    if get_settings().chat_context_local_worker and get_settings().chat_context_mode != "off":
        from backend.context_maintenance import maintain_local
        context_task = asyncio.create_task(maintain_local())
    try:
        yield
    finally:
        if context_task:
            context_task.cancel()
            await asyncio.gather(context_task, return_exceptions=True)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


app = FastAPI(title=get_settings().app_name, version="1.0.0", lifespan=lifespan)
from backend.observability import instrument, trace_identifier

app.middleware("http")(instrument)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins.split(","), allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def request_security(request: Request, call_next):
    request.state.request_id = request.headers.get("X-Request-ID", secrets.token_hex(8))
    request.state.trace_id = trace_identifier(request.headers.get("traceparent"))
    exempt = {"/api/auth/login", "/api/auth/platform/login", "/api/auth/password/forgot", "/api/auth/password/reset"}
    exempt.update({"/api/auth/register", "/api/auth/verify-email"})
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith("/api/"):
        invitation_path = (request.url.path.startswith("/api/auth/invitations/")
                           or request.url.path.startswith("/api/auth/platform-invitations/"))
        if request.url.path not in exempt and not invitation_path:
            cookie, header = request.cookies.get(CSRF_COOKIE), request.headers.get("X-CSRF-Token")
            if not cookie or not header or not secrets.compare_digest(cookie, header):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF 校验失败"},
                    headers={
                        "X-Request-ID": request.state.request_id,
                        "X-Trace-ID": request.state.trace_id,
                    },
                )
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Trace-ID"] = request.state.trace_id
    return response


app.include_router(auth_router)
app.include_router(onboarding_router)
app.include_router(member_import_router)
app.include_router(company_router)
app.include_router(platform_router)
app.include_router(project_access_router)
app.include_router(support_router)
app.include_router(platform_support_router)
app.include_router(delivery_router)
app.include_router(knowledge_router)
app.include_router(project_task_router)
app.include_router(chat_router)
from backend.memory_items import router as memory_router

app.include_router(memory_router)
from backend.chat_mcp import router as mcp_router

app.include_router(mcp_router)
app.include_router(retrieval_status_router)


@app.exception_handler(IntegrityError)
async def identity_conflict(request: Request, exc: IntegrityError):
    return JSONResponse(status_code=409, content={"detail": "数据发生并发冲突或信息重复，请刷新后重试"})


def envelope(request: Request, data: object) -> Envelope:
    return Envelope(data=data, request_id=request.state.request_id, trace_id=request.state.trace_id)


async def project_or_404(session: AsyncSession, project_id: str, user: Principal) -> Project:
    return await accessible_project_or_404(session, project_id, user)


async def run_or_404(session: AsyncSession, run_id: str, user: Principal) -> AgentRun:
    run = await session.scalar(select(AgentRun).where(AgentRun.id == run_id))
    if not run:
        raise HTTPException(404, "Run not found")
    project = await accessible_project_or_404(session, run.project_id, user, include_deleted=True)
    await require_project_permission(session, project, user, "run.view")
    return run


async def idempotent(session: AsyncSession, user: Principal, scope: str, key: str):
    return await session.scalar(select(IdempotencyRecord).where(IdempotencyRecord.tenant_id == user.tenant_id, IdempotencyRecord.scope == scope, IdempotencyRecord.key == key))


async def project_summary(session: AsyncSession, project: Project, user: Principal) -> dict:
    access = await project_access(session, project, user)
    document = await session.scalar(select(ProjectDocument).where(ProjectDocument.project_id == project.id, ProjectDocument.tenant_id == project.tenant_id))
    latest_run = None
    if "run.view" in access["permissions"]:
        latest_run = await session.scalar(select(AgentRun).where(AgentRun.project_id == project.id, AgentRun.tenant_id == project.tenant_id).order_by(AgentRun.created_at.desc()).limit(1))
    latest_approval = None
    if latest_run and "approval.view" in access["permissions"]:
        latest_approval = await session.scalar(select(Approval).where(Approval.run_id == latest_run.id, Approval.tenant_id == project.tenant_id).order_by(Approval.created_at.desc()).limit(1))
    status = project.lifecycle_status
    permissions = sorted(access["permissions"])
    active_run = bool(latest_run and latest_run.status in {"pending", "running", "waiting_approval", "preparing_materials", "blocked"})
    start_permission = "run.retry" if status == "blocked" else "run.start"
    return {
        "id": project.id, "name": project.name, "customer_name": project.customer_name,
        "status": status, "lifecycle_status": status, "version": project.version,
        "execution_status": latest_run.status if latest_run else None,
        "created_at": project.created_at.isoformat(), "document": document.content if document else None,
        "latest_run": ({"id": latest_run.id, "run_number": latest_run.run_number,
                        "retry_of_run_id": latest_run.retry_of_run_id, "status": latest_run.status,
                        "version": latest_run.version, "current_node": latest_run.current_node,
                        "updated_at": latest_run.updated_at.isoformat()} if latest_run else None),
        "latest_approval": ({"id": latest_approval.id, "kind": latest_approval.kind,
                             "status": latest_approval.status, "version": latest_approval.version,
                             "comment": latest_approval.comment,
                             "decided_by": latest_approval.decided_by} if latest_approval else None),
        "can_start": status in {"draft", "ready", "blocked"} and not active_run and start_permission in access["permissions"],
        "my_project_role": access["role"], "access_source": access["source"],
        "permissions": permissions,
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from backend.db import SessionLocal

    expected = ScriptDirectory.from_config(
        Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    ).get_current_head()
    try:
        async with asyncio.timeout(3):
            async with SessionLocal() as session:
                current = await session.scalar(text("SELECT version_num FROM alembic_version"))
        if current != expected:
            raise HTTPException(503, {"code": "SCHEMA_NOT_READY"})
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, {"code": "DATABASE_NOT_READY"}) from None
    return {"status": "ready", "schema_revision": current}


@app.get("/metrics", include_in_schema=False, response_class=PlainTextResponse)
async def metrics():
    from prometheus_client import generate_latest

    from backend.db import SessionLocal
    from backend.models import KnowledgeDocument, MailDelivery, WorkflowOutbox
    from backend.rag_v3_models import V3Document
    lines = [generate_latest().decode(), "saas_agent_up 1"]
    async with SessionLocal() as session:
        for model, column, name in ((AgentRun, AgentRun.status, "saas_runs"), (Approval, Approval.status, "saas_approvals"), (ImportJob, ImportJob.status, "saas_imports"), (MailDelivery, MailDelivery.status, "saas_mail")):
            for status, count in (await session.execute(select(column, func.count()).select_from(model).group_by(column))).all():
                lines.append(f'{name}{{status="{status}"}} {count}')
        backlog = await session.scalar(select(func.count()).select_from(WorkflowOutbox).where(WorkflowOutbox.processed.is_(False)))
        lines.append(f"saas_outbox_pending {backlog}")
        phases = {phase: 0 for phase in ("pending", "parsing", "indexing", "ready", "failed")}
        binary_counts = {}
        binary_parse_ms = {fmt: [] for fmt in ("pdf", "pptx", "xlsx")}
        ocr_pages = 0
        oldest = None
        # The monitoring request has no tenant principal. Query each tenant under
        # its RLS context so the aggregate reflects the actual queue state.
        tenant_ids = (list(await session.scalars(select(Tenant.id)))
                      if session.get_bind().dialect.name == "postgresql" else [None])
        for tenant_id in tenant_ids:
            if tenant_id:
                await session.execute(
                    text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                    {"tenant_id": tenant_id},
                )
            for phase, count in (await session.execute(
                select(V3Document.phase, func.count()).group_by(V3Document.phase)
            )).all():
                if phase in phases:
                    phases[phase] += count
            for filename, phase, document_metrics in (await session.execute(
                select(V3Document.filename, V3Document.phase, V3Document.metrics)
            )).all():
                fmt = filename.rsplit(".", 1)[-1].lower()
                if fmt not in binary_parse_ms:
                    continue
                binary_counts[fmt, phase] = binary_counts.get((fmt, phase), 0) + 1
                if phase == "ready" and isinstance(document_metrics, dict):
                    parse_ms = document_metrics.get("parse_ms")
                    if isinstance(parse_ms, (int, float)) and math.isfinite(parse_ms):
                        binary_parse_ms[fmt].append(parse_ms)
                    ocr_pages += document_metrics.get("ocr_pages", 0)
            tenant_oldest = await session.scalar(select(func.min(KnowledgeDocument.created_at))
                .join(V3Document, V3Document.origin_id == KnowledgeDocument.id)
                .where(V3Document.phase.in_(("pending", "parsing", "indexing"))))
            if tenant_oldest and (oldest is None or tenant_oldest < oldest):
                oldest = tenant_oldest
        for phase, count in phases.items():
            lines.append(f'saas_rag_v3_documents{{phase="{phase}"}} {count}')
        for (fmt, phase), count in sorted(binary_counts.items()):
            lines.append(f'saas_rag_v3_binary_documents{{format="{fmt}",phase="{phase}"}} {count}')
        for fmt, values in binary_parse_ms.items():
            if values:
                values.sort()
                lines.append(f'saas_rag_v3_parse_p95_ms{{format="{fmt}"}} {values[math.ceil(len(values) * 0.95) - 1]}')
        lines.append(f"saas_rag_v3_ocr_pages_indexed {ocr_pages}")
        age = max(0, (datetime.now(UTC) - oldest).total_seconds()) if oldest else 0
        lines.append(f"saas_rag_v3_oldest_pending_seconds {age}")
    return "\n".join(lines) + "\n"


@app.post("/api/projects")
async def create_project(payload: ProjectCreate, request: Request, key: str = Depends(idempotency_key), user: Principal = Depends(require_company_admin), session: AsyncSession = Depends(get_session)):
    existing = await idempotent(session, user, "create_project", key)
    if existing:
        return envelope(request, existing.response)
    company_name = payload.customer_name
    if user.workspace_kind == "personal" and (
        payload.assisting_company_id or (payload.company_id and str(payload.company_id) != user.tenant_id)
    ):
        raise HTTPException(403, "个人项目不能绑定企业或企业协作")
    if payload.company_id:
        company = await session.get(Tenant, str(payload.company_id))
        if not company or company.status != "active" or company.deleted_at is not None:
            raise HTTPException(404, "所选公司不存在或已停用")
        company_name = company.name
    assisting_company_id = str(payload.assisting_company_id) if payload.assisting_company_id else None
    if assisting_company_id:
        if assisting_company_id == user.tenant_id:
            raise HTTPException(409, "项目归属公司不能作为协助公司")
        assisting_company = await session.get(Tenant, assisting_company_id)
        if not assisting_company or assisting_company.kind != "company" or assisting_company.status != "active" or assisting_company.deleted_at is not None:
            raise HTTPException(404, "所选协助公司不存在或已停用")
    document = payload.model_dump(mode="json")
    document["customer_name"] = company_name
    project = Project(tenant_id=user.tenant_id, name=payload.name, customer_name=company_name, requirements_text=payload.requirements_text, created_by=user.user_id, owner_user_id=user.user_id)
    session.add(project)
    await session.flush()
    session.add(ProjectDocument(tenant_id=user.tenant_id, project_id=project.id, content=document))
    if get_settings().rag_mode == "real":
        from backend.retrieval_sources import sync_project
        await sync_project(session, project)
    session.add(ProjectMembership(
        project_id=project.id,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        project_role="project_manager",
        primary_role_code="project_manager",
        granted_by=user.user_id,
    ))
    if assisting_company_id:
        collaboration = ProjectCollaboration(
            project_id=project.id,
            owner_tenant_id=user.tenant_id,
            tenant_id=assisting_company_id,
            invited_by=user.user_id,
        )
        session.add(collaboration)
        await session.flush()
        session.add(audit_event(request, user, "project.collaboration_invited", collaboration.id, {
            "project_id": project.id, "tenant_id": assisting_company_id,
        }))
    data = await project_summary(session, project, user)
    session.add(IdempotencyRecord(tenant_id=user.tenant_id, scope="create_project", key=key, response=data))
    session.add(audit_event(request, user, "project.created", project.id, {"name": project.name}))
    await session.commit()
    return envelope(request, data)


@app.get("/api/projects")
async def list_projects(request: Request, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    projects = (await session.scalars(select(Project).where(Project.deleted_at.is_(None), accessible_project_filter(user)).order_by(Project.created_at.desc()))).all()
    return envelope(request, [await project_summary(session, project, user) for project in projects])


@app.get("/api/projects/{project_id}")
async def get_project(project_id: UUID, request: Request, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    return envelope(request, await project_summary(session, await project_or_404(session, str(project_id), user), user))


@app.get("/api/tasks")
async def my_tasks(request: Request, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    runs = (await session.execute(select(AgentRun, Project.name).join(Project, AgentRun.project_id == Project.id).where(accessible_project_filter(user), Project.deleted_at.is_(None), AgentRun.status.in_(["preparing_materials", "waiting_approval", "blocked", "failed", "cancelled"])).order_by(AgentRun.updated_at.desc()))).all()
    tasks = []
    for run, project_name in runs:
        latest = await session.scalar(select(func.max(AgentRun.run_number)).where(AgentRun.project_id == run.project_id))
        if latest != run.run_number:
            continue
        actions = await run_actions(session, run, user)
        if actions:
            tasks.append({"run_id": run.id, "project_id": run.project_id, "project_name": project_name,
                          "run_number": run.run_number, "current_node": run.current_node,
                          "status": run.status, "reason": run.state.get("blocking_reason"), "actions": actions})
    return envelope(request, tasks)


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: UUID, request: Request, key: str = Depends(idempotency_key), user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    existing = await idempotent(session, user, "delete_project", key)
    if existing:
        return envelope(request, existing.response)
    project = await project_or_404(session, str(project_id), user)
    await require_project_permission(session, project, user, "project.delete")
    project.deleted_at, project.deleted_by = datetime.now(UTC), user.user_id
    data = {"id": project.id, "deleted": True, "recoverable_days": 30}
    session.add(IdempotencyRecord(tenant_id=user.tenant_id, scope="delete_project", key=key, response=data))
    session.add(audit_event(request, user, "project.deleted", project.id, {"name": project.name, "soft_delete": True}))
    await session.commit()
    return envelope(request, data)


@app.post("/api/projects/{project_id}/runs")
async def start_run(project_id: UUID, request: Request, key: str = Depends(idempotency_key), user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    project = await project_or_404(session, str(project_id), user)
    permission = "run.retry" if project.lifecycle_status == "blocked" else "run.start"
    await require_project_permission(session, project, user, permission)
    existing = await idempotent(session, user, "start_run", key)
    if existing:
        return envelope(request, existing.response)
    await session.execute(select(Project.id).where(Project.id == project.id).with_for_update())
    latest_run = await session.scalar(select(AgentRun).where(AgentRun.project_id == project.id, AgentRun.tenant_id == project.tenant_id).order_by(AgentRun.run_number.desc()).limit(1))
    if project.lifecycle_status in {"completed", "cancelled", "archived"}:
        raise HTTPException(409, "已完成的项目不能再次启动实施流程")
    if latest_run and latest_run.status in {"pending", "running", "waiting_approval", "preparing_materials", "blocked"}:
        raise HTTPException(409, "该项目已有进行中的实施流程，请进入执行详情查看")
    transition_project(project, ProjectLifecycle.IN_PROGRESS)
    next_run_number = (await session.scalar(
        select(func.max(AgentRun.run_number)).where(AgentRun.project_id == project.id)
    ) or 0) + 1
    run = AgentRun(
        tenant_id=project.tenant_id,
        project_id=project.id,
        started_by=user.user_id,
        run_number=next_run_number,
        retry_of_run_id=(latest_run.id if latest_run and latest_run.status in {"failed", "cancelled"} else None),
        trace_id=request.state.trace_id,
    )
    session.add(run)
    await session.flush()
    if get_settings().execution_mode == "inline":
        transition_run(run, RunLifecycle.RUNNING)
    run.state = ImplementationGraphState(project_id=UUID(project.id), run_id=UUID(run.id), status=RunStatus(run.status), engine_version=get_settings().agent_engine).model_dump(mode="json")
    await session.flush()
    await dispatch(session, run)
    data = {"id": run.id, "run_number": run.run_number, "retry_of_run_id": run.retry_of_run_id,
            "status": run.status, "version": run.version, "current_node": run.current_node}
    session.add(IdempotencyRecord(tenant_id=user.tenant_id, scope="start_run", key=key, response=data))
    await session.commit()
    return envelope(request, data)


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(
    run_id: UUID,
    request: Request,
    key: str = Depends(idempotency_key),
    user: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
):
    existing = await idempotent(session, user, "cancel_run", key)
    if existing:
        return envelope(request, existing.response)
    run = await run_or_404(session, str(run_id), user)
    project = await project_or_404(session, run.project_id, user)
    await require_project_permission(session, project, user, "run.cancel")
    locked_run = await session.scalar(
        select(AgentRun).where(AgentRun.id == str(run_id)).with_for_update()
    )
    if not locked_run or locked_run.status not in {"pending", "running", "waiting_approval", "preparing_materials", "blocked"}:
        raise HTTPException(409, "只有进行中的 Run 可以取消")
    run = locked_run

    pending_approval = await session.scalar(
        select(Approval).where(
            Approval.run_id == run.id,
            Approval.status == "pending",
        ).with_for_update()
    )
    if pending_approval:
        pending_approval.status = "cancelled"
        pending_approval.comment = "Run 已由项目成员取消"
        pending_approval.decided_by = user.subject
        pending_approval.decided_at = datetime.now(UTC)
        pending_approval.version += 1

    state = ImplementationGraphState.model_validate(run.state)
    state.status = RunStatus.CANCELLED
    state.pending_approval_id = None
    state.updated_at = datetime.now(UTC)
    transition_run(run, RunLifecycle.CANCELLED)
    if project.lifecycle_status == ProjectLifecycle.IN_PROGRESS:
        transition_project(project, ProjectLifecycle.BLOCKED)
    run.state = state.model_dump(mode="json")
    run.updated_at = datetime.now(UTC)
    data = {"id": run.id, "status": run.status, "version": run.version,
            "project_status": project.lifecycle_status}
    session.add(IdempotencyRecord(
        tenant_id=user.tenant_id, scope="cancel_run", key=key, response=data
    ))
    session.add(audit_event(request, user, "run.cancelled", run.id, {
        "project_id": project.id, "run_number": run.run_number,
    }))
    await session.commit()
    return envelope(request, data)


from backend.schemas import ResumeRequest


@app.post("/api/runs/{run_id}/resume")
async def resume_run(run_id: UUID, payload: ResumeRequest, request: Request, key: str = Depends(idempotency_key), user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    from backend.agent_loop import REGISTRY, authorize
    from backend.agent_models import AgentAction
    run = await run_or_404(session, str(run_id), user)
    project = await project_or_404(session, run.project_id, user)
    await require_project_permission(session, project, user, "run.retry")
    existing = await idempotent(session, user, "resume_run", key)
    if existing:
        return envelope(request, existing.response)
    locked = await session.scalar(select(AgentRun).where(AgentRun.id == str(run_id)).with_for_update().execution_options(populate_existing=True))
    if not locked or locked.status != "blocked" or locked.state.get("engine_version") != "v2" or locked.version != payload.expected_version:
        raise HTTPException(409, "任务不可恢复或版本已变化，请刷新")
    run = locked
    await authorize(session, run, REGISTRY["create_project"])
    unknown = await session.scalar(select(AgentAction.id).where(AgentAction.run_id == run.id, AgentAction.status == "started").limit(1))
    if unknown:
        raise HTTPException(409, "存在执行状态不明的动作，请先核对执行凭证")
    state = ImplementationGraphState.model_validate(run.state)
    state.agent.resume_count += 1
    state.agent.rounds, state.agent.replans, state.agent.retries = 0, 0, {}
    # Budget remains cumulative across resumes. Operators must explicitly raise
    # the configured budget if the original allocation has been consumed.
    state.agent.evaluation = None
    state.status, state.blocking_reason = RunStatus.RUNNING, None
    transition_run(run, RunLifecycle.RUNNING)
    transition_project(project, ProjectLifecycle.IN_PROGRESS)
    run.state = state.model_dump(mode="json")
    session.add(audit_event(request, user, "run.resumed", run.id, {"reason": payload.reason, "resume_count": state.agent.resume_count}))
    await dispatch(session, run)
    data = {"id": run.id, "status": run.status, "version": run.version}
    session.add(IdempotencyRecord(tenant_id=user.tenant_id, scope="resume_run", key=key, response=data))
    await session.commit()
    return envelope(request, data)


@app.get("/api/runs/{run_id}")
async def get_run(run_id: UUID, request: Request, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    run = await run_or_404(session, str(run_id), user)
    return envelope(request, {"id": run.id, "project_id": run.project_id,
                              "run_number": run.run_number, "retry_of_run_id": run.retry_of_run_id,
                              "status": run.status, "version": run.version,
                              "current_node": run.current_node, "state": run.state,
                              "blocking_reason": run.state.get("blocking_reason"),
                              "allowed_actions": await run_actions(session, run, user),
                              "trace_id": run.trace_id})


@app.get("/api/runs/{run_id}/steps")
async def get_run_steps(run_id: UUID, request: Request, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    run = await run_or_404(session, str(run_id), user)
    rows = (await session.scalars(select(AgentStep).where(AgentStep.run_id == str(run_id), AgentStep.tenant_id == run.tenant_id).order_by(AgentStep.sequence.asc()))).all()
    return envelope(request, [{"id": x.id, "sequence": x.sequence, "node": x.node,
                               "status": x.status, "detail": x.detail,
                               "created_at": x.created_at.isoformat()} for x in rows])


@app.get("/api/runs/{run_id}/actions")
async def get_run_actions(run_id: UUID, request: Request, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    from backend.agent_models import AgentAction
    run = await run_or_404(session, str(run_id), user)
    rows = (await session.scalars(select(AgentAction).where(AgentAction.run_id == run.id, AgentAction.tenant_id == run.tenant_id).order_by(AgentAction.created_at, AgentAction.id))).all()
    return envelope(request, [{"id": a.id, "stage": a.stage, "milestone_id": a.milestone_id, "tool": a.tool,
        "status": a.status, "result": a.result, "created_at": a.created_at.isoformat()} for a in rows])


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: UUID, request: Request, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    await run_or_404(session, str(run_id), user)
    await session.commit()
    async def stream():
        from backend.db import SessionLocal
        last = ""
        for _ in range(120):
            try:
                async with SessionLocal() as poll_session:
                    viewer = await current_principal(request, poll_session)
                    run = await run_or_404(poll_session, str(run_id), viewer)
                    current = json.dumps({"status": run.status, "node": run.current_node, "trace_id": run.trace_id, "version": run.version}, ensure_ascii=False)
                    status = run.status
                    await poll_session.commit()
            except HTTPException:
                yield 'event: error\ndata: {"message":"access revoked"}\n\n'
                return
            if current != last:
                yield f"event: run\ndata: {current}\n\n"
                last = current
            if status in {"succeeded", "failed", "cancelled"}:
                return
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/approvals")
async def list_approvals(request: Request, run_id: UUID | None = None, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    query = (
        select(Approval)
        .join(AgentRun, AgentRun.id == Approval.run_id)
        .join(Project, Project.id == AgentRun.project_id)
        .where(accessible_project_filter(user))
        .order_by(Approval.created_at.desc())
    )
    if run_id:
        query = query.where(Approval.run_id == str(run_id))
    rows = (await session.scalars(query)).all()
    visible = []
    for item in rows:
        run = await session.get(AgentRun, item.run_id)
        project = await session.get(Project, run.project_id) if run else None
        if not project:
            continue
        try:
            await require_project_permission(session, project, user, "approval.view")
        except HTTPException:
            continue
        visible.append({"id": item.id, "run_id": item.run_id, "kind": item.kind,
                        "status": item.status, "version": item.version, "payload": item.payload,
                        "comment": item.comment, "decided_by": item.decided_by,
                        "decided_at": item.decided_at.isoformat() if item.decided_at else None,
                        "created_at": item.created_at.isoformat()})
    return envelope(request, visible)


@app.post("/api/approvals/{approval_id}/decision")
async def decide(approval_id: UUID, payload: ApprovalDecision, request: Request, key: str = Depends(idempotency_key), user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    existing = await idempotent(session, user, "approval", key)
    if existing:
        return envelope(request, existing.response)
    approval = await session.scalar(
        select(Approval).where(Approval.id == str(approval_id)).with_for_update()
    )
    if not approval or approval.status != "pending":
        raise HTTPException(409, "Approval is not pending")
    if approval.version != payload.expected_version:
        raise HTTPException(409, "Approval version conflict; refresh before deciding")
    run = await session.get(AgentRun, approval.run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    project = await project_or_404(session, run.project_id, user)
    await require_project_permission(session, project, user, "approval.decide")
    owner_space = await session.get(Tenant, project.tenant_id)
    personal_confirmation = bool(owner_space and owner_space.kind == "personal"
                                 and owner_space.personal_owner_id == user.user_id and project.tenant_id == user.tenant_id)
    if not personal_confirmation and (run.started_by == user.user_id or approval.requested_by in {user.subject, user.user_id}):
        raise HTTPException(403, "Requester cannot approve their own high-risk operation")
    if run.status != "waiting_approval":
        raise HTTPException(409, "Run 不处于等待审批状态")
    if payload.decision == "rejected" and not payload.comment.strip():
        raise HTTPException(422, "驳回必须填写整改意见")
    if payload.decision == "approved":
        current = await delivery.material(session, project, run, ImplementationGraphState.model_validate(run.state), approval.kind)
        if current != approval.payload:
            raise HTTPException(409, "审批材料已变化，请取消本次执行并重新制定方案")
        if approval.kind == "acceptance" and not current["body"]["ready"]:
            raise HTTPException(409, "上线检查尚未通过")
    approval.status, approval.comment, approval.decided_by = payload.decision, payload.comment, user.subject
    approval.decided_at, approval.version = datetime.now(UTC), approval.version + 1
    run = await resume_after_approval(session, approval, payload.decision)
    data = {"approval_id": approval.id, "run_id": run.id, "run_status": run.status, "current_node": run.current_node}
    session.add(IdempotencyRecord(tenant_id=user.tenant_id, scope="approval", key=key, response=data))
    session.add(audit_event(request, user, f"approval.{payload.decision}", approval.id, {"kind": approval.kind, "comment": payload.comment, "personal_confirmation": personal_confirmation}))
    await session.commit()
    return envelope(request, data)


@app.post("/api/imports/validate")
async def validate_import(payload: ImportValidateRequest, request: Request, key: str = Depends(idempotency_key), user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    existing = await idempotent(session, user, "validate_import", key)
    if existing:
        return envelope(request, existing.response)
    project = await project_or_404(session, str(payload.project_id), user)
    await require_project_permission(session, project, user, "import.validate")
    run = await session.scalar(select(AgentRun).where(AgentRun.id == str(payload.run_id), AgentRun.project_id == project.id).with_for_update())
    if not run or run.tenant_id != project.tenant_id:
        raise HTTPException(404, "Run 不存在")
    if run.status != "preparing_materials":
        raise HTTPException(409, "只能在材料准备阶段上传 CSV；已审批文件不可替换")
    result = await delivery.validate_csv(session, project, payload.csv_text)
    job = ImportJob(tenant_id=project.tenant_id, project_id=project.id, run_id=run.id, status="valid" if result["valid"] else "invalid", source_hash=hashlib.sha256(payload.csv_text.encode()).hexdigest(), validation=result, csv_text=payload.csv_text)
    session.add(job)
    await session.flush()
    if result["valid"]:
        state = ImplementationGraphState.model_validate(run.state)
        state.import_job_id = UUID(job.id)
        state.blocking_reason = None
        state.status = RunStatus.RUNNING
        run.state = state.model_dump(mode="json")
        transition_run(run, RunLifecycle.RUNNING)
        await dispatch(session, run)
    data = {"job_id": job.id, "run_status": run.status, **result}
    session.add(IdempotencyRecord(tenant_id=user.tenant_id, scope="validate_import", key=key, response=data))
    await session.commit()
    return envelope(request, data)


@app.post("/api/imports/{job_id}/execute")
async def execute_import(job_id: UUID, request: Request, key: str = Depends(idempotency_key), approval_id: UUID | None = None, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    existing = await idempotent(session, user, "execute_import", key)
    if existing:
        return envelope(request, existing.response)
    job = await session.scalar(select(ImportJob).where(ImportJob.id == str(job_id)))
    if not job:
        raise HTTPException(404, "Import job not found")
    project = await project_or_404(session, job.project_id, user)
    await require_project_permission(session, project, user, "import.execute")
    if not approval_id or not job.run_id:
        raise HTTPException(403, "An approved import approval is required")
    run = await session.scalar(select(AgentRun).where(AgentRun.id == job.run_id).with_for_update())
    if not run or run.status in {"failed", "cancelled"}:
        raise HTTPException(409, "终止任务必须通过新的 Run 整改")
    job.result = await delivery.execute_csv(session, project, run, ImplementationGraphState.model_validate(run.state), job, str(approval_id))
    data = {"job_id": job.id, **job.result}
    session.add(IdempotencyRecord(tenant_id=user.tenant_id, scope="execute_import", key=key, response=data))
    session.add(audit_event(request, user, "import.executed", job.id, data))
    await session.commit()
    return envelope(request, data)


async def run_actions(session: AsyncSession, run: AgentRun, user: Principal) -> list[str]:
    project = await accessible_project_or_404(session, run.project_id, user, include_deleted=True)
    if project.deleted_at:
        return []
    actions = []
    owner_space = await session.get(Tenant, project.tenant_id)
    personal_confirmation = bool(owner_space and owner_space.kind == "personal"
                                 and owner_space.personal_owner_id == user.user_id and project.tenant_id == user.tenant_id)
    for action, permission, available in (
        ("upload_csv", "import.validate", run.status == "preparing_materials"),
        ("cancel", "run.cancel", run.status in {"pending", "running", "preparing_materials", "waiting_approval", "blocked"}),
        ("resume", "run.retry", run.status == "blocked" and run.state.get("engine_version") == "v2"),
        ("approve", "approval.decide", run.status == "waiting_approval" and (run.started_by != user.user_id or personal_confirmation)),
        ("retry", "run.retry", run.status in {"failed", "cancelled"}),
    ):
        if available:
            try:
                await require_project_permission(session, project, user, permission)
                actions.append(action)
            except HTTPException:
                continue
    return actions


async def latest_run_for_project(session: AsyncSession, project_id: str, user: Principal):
    project = await project_or_404(session, project_id, user)
    run = await session.scalar(select(AgentRun).where(AgentRun.project_id == project_id, AgentRun.tenant_id == project.tenant_id).order_by(AgentRun.created_at.desc()))
    if not run:
        raise HTTPException(404, "Run not found")
    return run


@app.get("/api/projects/{project_id}/plan")
async def project_plan(project_id: UUID, request: Request, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    return envelope(request, (await latest_run_for_project(session, str(project_id), user)).state.get("plan"))


@app.get("/api/projects/{project_id}/go-live-report")
async def report(project_id: UUID, request: Request, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    return envelope(request, (await latest_run_for_project(session, str(project_id), user)).state.get("acceptance_report"))


# Register last so API routes take precedence over browser history routes.
from backend.frontend import mount_frontend

mount_frontend(app)
