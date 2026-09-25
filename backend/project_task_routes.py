from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.access import accessible_project_or_404, project_access, require_project_permission
from backend.audit import audit_event
from backend.db import get_session
from backend.models import (
    KnowledgeDocument,
    ProjectCollaboration,
    ProjectMembership,
    ProjectTask,
    ProjectTaskDocument,
    ProjectTaskReview,
    Tenant,
    TenantMembership,
    User,
    utcnow,
)
from backend.security import Principal, current_principal

router = APIRouter(prefix="/api/projects/{project_id}", tags=["project tasks"])
MANAGER_ROLES = {"company_admin", "project_manager", "implementation_consultant"}


class TaskInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=10000)
    assignee_id: UUID | None = None
    due_date: date | None = None


class TaskUpdate(TaskInput):
    expected_version: int = Field(ge=1)


class TaskAction(BaseModel):
    expected_version: int = Field(ge=1)
    comment: str = Field(default="", max_length=2000)


class TaskReviewDecision(TaskAction):
    decision: Literal["approved", "rejected"]


def participants(project):
    return (select(User.id, User.display_name)
        .join(ProjectMembership, ProjectMembership.user_id == User.id)
        .join(TenantMembership, (TenantMembership.user_id == User.id) &
              (TenantMembership.tenant_id == ProjectMembership.tenant_id))
        .where(ProjectMembership.project_id == project.id,
               ProjectMembership.status == "active", ProjectMembership.primary_role_code != "viewer",
               TenantMembership.status == "active", User.status == "active",
               or_(ProjectMembership.tenant_id == project.tenant_id, exists().where(
                   ProjectCollaboration.project_id == project.id,
                   ProjectCollaboration.tenant_id == ProjectMembership.tenant_id,
                   ProjectCollaboration.status == "active")))
        .order_by(User.display_name))


def task_view(item, documents=(), review=None, names=None):
    names = names or {}
    data = {key: getattr(item, key) for key in (
        "id", "project_id", "title", "description", "status", "blocking_reason",
        "assignee_id", "due_date", "created_by", "version", "created_at", "updated_at")}
    data["documents"] = [{"id": d.id, "title": d.title, "version": d.version,
                          "source": d.source, "index_status": d.index_status} for d in documents]
    data["latest_review"] = None if not review else {
        "id": review.id, "round": review.round, "status": review.status,
        "version": review.version, "submission_comment": review.submission_comment,
        "decision_comment": review.decision_comment, "submitted_by": review.submitted_by,
        "submitter_name": names.get(review.submitted_by, ""), "decided_by": review.decided_by,
        "decider_name": names.get(review.decided_by, ""), "created_at": review.created_at,
        "decided_at": review.decided_at,
    }
    return data


async def writable_project(session, project_id, user):
    project = await accessible_project_or_404(session, str(project_id), user)
    access = await require_project_permission(session, project, user, "project.task.write")
    if user.session_context != "customer":
        raise HTTPException(403, "只允许项目成员操作")
    if project.lifecycle_status in {"completed", "archived", "cancelled"}:
        raise HTTPException(409, "项目已结束，任务以只读方式保留")
    return project, access


async def validate_assignee(session, project, assignee_id):
    if assignee_id and not await session.scalar(participants(project).where(User.id == str(assignee_id))):
        raise HTTPException(422, "负责人必须是本项目有效的非只读成员")


async def locked_task(session, project, task_id, expected_version):
    item = await session.scalar(select(ProjectTask).where(
        ProjectTask.project_id == project.id, ProjectTask.id == str(task_id)).with_for_update())
    if not item:
        raise HTTPException(404, "任务不存在")
    if item.version != expected_version:
        raise HTTPException(409, "任务已被其他成员更新，请刷新后重试")
    return item


def require_worker(item, user, access):
    if (access["role"] not in MANAGER_ROLES and item.assignee_id not in {None, user.user_id}
            and item.created_by != user.user_id):
        raise HTTPException(403, "只有负责人或项目管理成员可以推进此任务")


@router.get("/tasks")
async def list_tasks(project_id: UUID, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    project = await accessible_project_or_404(session, str(project_id), user)
    access = await project_access(session, project, user)
    rows = (await session.scalars(select(ProjectTask).where(ProjectTask.project_id == project.id)
                                 .order_by(ProjectTask.created_at.desc(), ProjectTask.id))).all()
    members = (await session.execute(participants(project))).all()
    task_ids = [item.id for item in rows]
    document_map = {task_id: [] for task_id in task_ids}
    if task_ids:
        query = (select(ProjectTaskDocument.task_id, KnowledgeDocument)
                 .join(KnowledgeDocument, KnowledgeDocument.id == ProjectTaskDocument.document_id)
                 .where(ProjectTaskDocument.task_id.in_(task_ids), KnowledgeDocument.active.is_(True))
                 .order_by(ProjectTaskDocument.created_at))
        for task_id, document in (await session.execute(query)).all():
            document_map[task_id].append(document)
    review_map = {}
    reviews = (await session.scalars(select(ProjectTaskReview).where(ProjectTaskReview.task_id.in_(task_ids))
                                     .order_by(ProjectTaskReview.task_id, ProjectTaskReview.round))).all() if task_ids else []
    for review in reviews:
        review_map[review.task_id] = review
    user_ids = {value for review in reviews for value in (review.submitted_by, review.decided_by) if value}
    names = dict((await session.execute(select(User.id, User.display_name).where(User.id.in_(user_ids)))).all()) if user_ids else {}
    return {"data": {"tasks": [task_view(item, document_map[item.id], review_map.get(item.id), names) for item in rows],
                     "members": [{"id": m.id, "display_name": m.display_name} for m in members],
                     "current_user_id": user.user_id, "can_approve": "approval.decide" in access["permissions"]}}


@router.post("/tasks")
async def create_task(project_id: UUID, payload: TaskInput, request: Request,
                      user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    project, _ = await writable_project(session, project_id, user)
    await validate_assignee(session, project, payload.assignee_id)
    item = ProjectTask(project_id=project.id, tenant_id=project.tenant_id,
                       created_by=user.user_id, **payload.model_dump(mode="json"))
    session.add(item)
    await session.flush()
    session.add(audit_event(request, user, "project.task_created", item.id, {"project_id": project.id, "title": item.title}))
    await session.commit()
    return {"data": task_view(item)}


@router.put("/tasks/{task_id}")
async def update_task(project_id: UUID, task_id: UUID, payload: TaskUpdate, request: Request,
                      user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    project, access = await writable_project(session, project_id, user)
    item = await locked_task(session, project, task_id, payload.expected_version)
    require_worker(item, user, access)
    if item.status in {"pending_review", "done"}:
        raise HTTPException(409, "审批中或已通过的任务不能修改")
    await validate_assignee(session, project, payload.assignee_id)
    for key, value in payload.model_dump(mode="json", exclude={"expected_version"}).items():
        setattr(item, key, value)
    item.version += 1
    item.updated_at = utcnow()
    session.add(audit_event(request, user, "project.task_updated", item.id,
                            {"project_id": project.id, "version": item.version}))
    await session.commit()
    return {"data": task_view(item)}


@router.post("/tasks/{task_id}/actions/{action}")
async def task_action(project_id: UUID, task_id: UUID, action: Literal["start", "block", "resume", "submit_review"],
                      payload: TaskAction, request: Request, user: Principal = Depends(current_principal),
                      session: AsyncSession = Depends(get_session)):
    project, access = await writable_project(session, project_id, user)
    item = await locked_task(session, project, task_id, payload.expected_version)
    require_worker(item, user, access)
    if action == "start":
        if item.status not in {"todo", "changes_requested"}:
            raise HTTPException(409, "当前状态不能开始或继续任务")
        if item.assignee_id is None:
            await validate_assignee(session, project, user.user_id)
            item.assignee_id = user.user_id
        item.status, item.blocking_reason = "in_progress", ""
    elif action == "block":
        if item.status not in {"todo", "in_progress", "changes_requested"}:
            raise HTTPException(409, "当前状态不能报告阻塞")
        if not payload.comment.strip():
            raise HTTPException(422, "请填写阻塞原因")
        item.status, item.blocking_reason = "blocked", payload.comment.strip()
    elif action == "resume":
        if item.status != "blocked":
            raise HTTPException(409, "只有已阻塞任务可以恢复")
        item.status, item.blocking_reason = "in_progress", ""
    else:
        if item.status not in {"in_progress", "changes_requested"}:
            raise HTTPException(409, "只有进行中或需修改的任务可以提交审批")
        document_ids = list(await session.scalars(select(ProjectTaskDocument.document_id).where(
            ProjectTaskDocument.task_id == item.id)))
        if not document_ids:
            raise HTTPException(422, "提交审批前至少需要提交一份任务文档")
        round_number = 1 + (await session.scalar(select(func.max(ProjectTaskReview.round)).where(
            ProjectTaskReview.task_id == item.id)) or 0)
        session.add(ProjectTaskReview(tenant_id=project.tenant_id, project_id=project.id, task_id=item.id,
            round=round_number, document_ids=document_ids, submission_comment=payload.comment.strip(),
            submitted_by=user.user_id))
        item.status = "pending_review"
    item.version += 1
    item.updated_at = utcnow()
    session.add(audit_event(request, user, f"project.task_{action}", item.id,
                            {"project_id": project.id, "comment": payload.comment, "version": item.version}))
    await session.commit()
    return {"data": task_view(item)}


@router.post("/tasks/{task_id}/reviews/{review_id}/decision")
async def decide_task_review(project_id: UUID, task_id: UUID, review_id: UUID, payload: TaskReviewDecision,
                             request: Request, user: Principal = Depends(current_principal),
                             session: AsyncSession = Depends(get_session)):
    project = await accessible_project_or_404(session, str(project_id), user)
    await require_project_permission(session, project, user, "approval.decide")
    item = await locked_task(session, project, task_id, payload.expected_version)
    review = await session.scalar(select(ProjectTaskReview).where(
        ProjectTaskReview.id == str(review_id), ProjectTaskReview.task_id == item.id).with_for_update())
    if not review or review.status != "pending" or item.status != "pending_review":
        raise HTTPException(409, "该任务没有待处理的审批")
    owner_space = await session.get(Tenant, project.tenant_id)
    personal_confirmation = bool(owner_space and owner_space.kind == "personal"
                                 and owner_space.personal_owner_id == user.user_id and project.tenant_id == user.tenant_id)
    if review.submitted_by == user.user_id and not personal_confirmation:
        raise HTTPException(403, "提交人不能审批自己的任务")
    if payload.decision == "rejected" and not payload.comment.strip():
        raise HTTPException(422, "驳回必须填写修改意见")
    review.status, review.decision_comment = payload.decision, payload.comment.strip()
    review.decided_by, review.decided_at, review.version = user.user_id, utcnow(), review.version + 1
    item.status = "done" if payload.decision == "approved" else "changes_requested"
    item.version += 1
    item.updated_at = utcnow()
    session.add(audit_event(request, user, f"project.task_review_{payload.decision}", review.id,
                            {"project_id": project.id, "task_id": item.id, "comment": payload.comment}))
    await session.commit()
    return {"data": task_view(item, review=review)}
