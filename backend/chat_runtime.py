"""Narrow read-only tools: filter and authorize before exposing any runtime facts."""
import hashlib
import json
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, select

from backend.access import accessible_project_filter, project_access
from backend.config import get_settings
from backend.models import (
    AgentRun,
    Approval,
    ImportJob,
    Project,
    ProjectArtifact,
    SaaSMember,
    SaaSWorkspace,
)


async def runtime_evidence(session, user, query: str, project_id: str | None) -> list[dict]:
    intent = any(word in query.lower() for word in ("当前", "现在", "我的", "我是谁", "我有", "我能", "我是什么", "进度", "状态", "卡在", "卡住", "待办", "权限", "多少项目", "多少个项目", "项目数量", "项目列表", "进行到", "完成了吗", "配置好", "xl-"))
    if not intent:
        return []
    selection = select(Project).where(accessible_project_filter(user), Project.deleted_at.is_(None))
    if project_id:
        selection = selection.where(Project.id == project_id)
    candidates = (await session.scalars(selection.order_by(Project.created_at.desc()).limit(51))).all()
    items = []
    for project in candidates[:50]:
        try:
            access = await project_access(session, project, user)
        except HTTPException as exc:
            if exc.status_code in {403, 404}:
                continue
            raise
        # Only use ids/names obtained from authorized rows to resolve user references.
        explicit = project.id in query or project.name in query or project.name.split('｜')[0].strip().lower() in query.lower()
        if project_id and project.id != project_id:
            continue
        items.append((project, access, explicit))
    if not project_id and any(explicit for _, _, explicit in items):
        items = [item for item in items if item[2]]
    details = []
    for project, access, _ in items[:6]:
        permissions = access['permissions']
        item = {"project": project.name, "project_status": project.lifecycle_status, "my_role": access['role'], "my_permissions": sorted(permissions)}
        if 'run.view' in permissions:
            run = await session.scalar(select(AgentRun).where(AgentRun.project_id == project.id).order_by(AgentRun.run_number.desc()).limit(1))
            if run:
                item['run'] = {"number": run.run_number, "status": run.status, "node": run.current_node, "engine": run.state.get('engine_version', 'legacy'), "page": f"/app/runs/{run.id}"}
                if run.state.get('blocking_reason'):
                    item['run']['blocking_reason'] = str(run.state['blocking_reason'])[:500]
                if 'approval.view' in permissions:
                    approvals = (await session.scalars(select(Approval).where(Approval.run_id == run.id, Approval.status == 'pending'))).all()
                    personal = user.workspace_kind == 'personal' and project.tenant_id == user.tenant_id
                    item['pending_approvals'] = [{"kind": a.kind, "version": a.version, "can_decide": 'approval.decide' in permissions and (personal or (run.started_by != user.user_id and a.requested_by not in {user.user_id, user.subject}))} for a in approvals]
                if 'import.view' in permissions:
                    jobs = (await session.scalars(select(ImportJob).where(ImportJob.run_id == run.id).limit(10))).all()
                    item['imports'] = [{"status": j.status, "rows": j.validation.get('row_count'), "error_count": len(j.validation.get('errors', [])),
                                        "first_errors": [{k: e.get(k) for k in ('row', 'field', 'message', 'suggestion')} for e in j.validation.get('errors', [])[:5]]} for j in jobs]
        if 'artifact.view' in permissions:
            item['artifact_count'] = await session.scalar(select(func.count()).select_from(ProjectArtifact).where(ProjectArtifact.project_id == project.id))
        if 'import.view' in permissions:
            item['actual_business_members'] = await session.scalar(select(func.count()).select_from(SaaSMember).join(SaaSWorkspace, SaaSMember.workspace_id == SaaSWorkspace.id).where(SaaSWorkspace.project_id == project.id))
        details.append(item)
    s = get_settings()
    snapshot = {"scope": "仅当前账号授权范围；结果最多50个候选、6个详情，不是平台全量统计", "workspace": user.tenant_name, "my_name": user.display_name,
                "company_role": user.company_role_code, "matched_projects": len(items), "truncated": len(candidates) > 50 or len(items) > 6,
                "model_mode": s.model_mode, "model_configured": bool(s.model_base_url and s.model_name and s.model_api_key), "projects": details}
    body = json.dumps(snapshot, ensure_ascii=False, separators=(',', ':'))
    return [{"id": "runtime:" + hashlib.sha256(body.encode()).hexdigest()[:16], "title": "当前授权空间与项目状态",
             "text": body, "source": "只读实时查询 · " + datetime.now(UTC).isoformat(), "kind": "runtime"}]
