"""Controlled, transactional tools for the persisted demonstration SaaS.

Business members are deliberately separate from identity/account users. All
effects and their receipts commit with the workflow checkpoint in one DB transaction.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from copy import deepcopy

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.imports import validate_member_csv
from backend.models import (
    AgentRun,
    Approval,
    AuditEvent,
    ImportJob,
    Project,
    ProjectArtifact,
    ProjectDocument,
    SaaSMember,
    SaaSWorkspace,
    ToolExecution,
)
from backend.schemas import AcceptanceReport, GoLiveCheckResult, ImplementationGraphState

ROLES = ["admin", "manager", "member", "viewer"]
ROLE_ALIASES = {"管理员": "admin", "项目经理": "manager", "成员": "member", "访客": "viewer"}


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def document(session: AsyncSession, project: Project) -> dict:
    item = await session.scalar(select(ProjectDocument).where(ProjectDocument.project_id == project.id))
    return item.content if item else {}


async def workspace(session: AsyncSession, project: Project) -> SaaSWorkspace:
    item = await session.scalar(select(SaaSWorkspace).where(SaaSWorkspace.project_id == project.id).with_for_update())
    if item is None:
        doc = await document(session, project)
        item = SaaSWorkspace(tenant_id=project.tenant_id, project_id=project.id, configuration={
            "name": project.customer_name, "departments": doc.get("departments", []),
            "roles": ROLES, "workflow.statuses": ["待办", "进行中", "完成"],
            "notifications.due_date": False, "templates": ["标准项目"], "custom_fields": [],
        })
        session.add(item)
        await session.flush()
    return item


async def migration_required(session: AsyncSession, project: Project) -> bool:
    doc = await document(session, project)
    scope = str(doc.get("migration_scope", "")).strip()
    if scope in {"无", "无需迁移", "不迁移"}:
        return False
    return bool(scope) or any(word in project.requirements_text.lower() for word in ("csv", "导入", "迁移"))


def csv_rows(job: ImportJob) -> list[dict]:
    mapping = {item["target"]: item["source"] for item in job.validation["mappings"]}
    values = []
    for row in csv.DictReader(io.StringIO(job.csv_text.lstrip("\ufeff"))):
        canonical = {field: (row.get(source) or "").strip() for field, source in mapping.items()}
        canonical["email"] = canonical["email"].lower()
        canonical["role"] = ROLE_ALIASES.get(canonical["role"], canonical["role"])
        values.append(canonical)
    return values


async def validate_csv(session: AsyncSession, project: Project, text: str) -> dict:
    result = validate_member_csv(text).model_dump(mode="json")
    if not result["valid"]:
        return result
    ws = await workspace(session, project)
    candidate = ImportJob(csv_text=text, validation=result)
    errors = result["errors"]
    if not result["row_count"]:
        errors.append({"row": 1, "field": "file", "message": "CSV 没有成员数据", "suggestion": "添加成员行"})
    existing = {m.email: m for m in (await session.scalars(select(SaaSMember).where(SaaSMember.workspace_id == ws.id))).all()}
    for number, row in enumerate(csv_rows(candidate), 2):
        for field, allowed in (("department", ws.configuration["departments"]), ("role", ROLES)):
            if row[field] not in allowed:
                errors.append({"row": number, "field": field, "message": "目标系统不存在该部门或角色", "suggestion": "可选值：" + ", ".join(allowed)})
        if any(len(row[field]) > limit for field, limit in (("name", 120), ("email", 160), ("department", 120), ("role", 40))):
            errors.append({"row": number, "field": "row", "message": "字段长度超限", "suggestion": "缩短字段内容"})
        member = existing.get(row["email"])
        if member and any(getattr(member, key) != value for key, value in row.items()):
            errors.append({"row": number, "field": "email", "message": "目标成员资料冲突", "suggestion": "使用一致资料或独立整改，不能覆盖现有成员"})
    result["valid"] = not errors
    return result


async def artifact(session: AsyncSession, project: Project, run: AgentRun, kind: str, title: str, content: str) -> ProjectArtifact:
    checksum = digest(content)
    existing = await session.scalar(select(ProjectArtifact).where(ProjectArtifact.run_id == run.id, ProjectArtifact.kind == kind, ProjectArtifact.checksum == checksum))
    if existing:
        return existing
    version = (await session.scalar(select(func.max(ProjectArtifact.version)).where(ProjectArtifact.project_id == project.id, ProjectArtifact.kind == kind)) or 0) + 1
    item = ProjectArtifact(tenant_id=project.tenant_id, project_id=project.id, run_id=run.id, kind=kind, title=title, version=version, checksum=checksum, content=content)
    session.add(item)
    await session.flush()
    from backend.storage import persist
    await persist(item)
    from backend.config import get_settings
    if get_settings().rag_mode == "real":
        from backend.retrieval_sources import sync_artifact
        await sync_artifact(session, item)
    return item


async def material(session: AsyncSession, project: Project, run: AgentRun, state: ImplementationGraphState, kind: str) -> dict:
    body: dict
    if kind == "plan":
        body = {"plan": state.plan.model_dump() if state.plan else None, "requirements": [r.model_dump() for r in state.requirements], "gaps": [g.model_dump() for g in state.gap_items]}
    elif kind == "configuration":
        body = {"changes": [c.model_dump() for c in state.configuration_changes]}
    elif kind == "import":
        job = await session.get(ImportJob, str(state.import_job_id)) if state.import_job_id else None
        if job and (job.run_id != run.id or job.project_id != project.id or job.tenant_id != project.tenant_id):
            raise HTTPException(409, "导入材料不属于本次执行")
        body = {"job_id": job.id, "source_hash": job.source_hash, "validation": job.validation} if job else {"skipped": True, "reason": "项目无需成员迁移"}
    else:
        report = await go_live_checks(session, project, run, state)
        body = report.model_dump()
    return {"project_id": project.id, "run_id": run.id, "kind": kind, "body": body, "material_hash": digest(body)}


async def require_receipt(session: AsyncSession, project: Project, run: AgentRun, state: ImplementationGraphState, kind: str, approval_id: str | None = None) -> Approval:
    query = select(Approval).where(Approval.run_id == run.id, Approval.tenant_id == project.tenant_id, Approval.kind == kind, Approval.status == "approved")
    if approval_id:
        query = query.where(Approval.id == approval_id)
    approval = await session.scalar(query.order_by(Approval.created_at.desc()))
    current = await material(session, project, run, state, kind)
    if not approval or approval.payload != current:
        raise HTTPException(403, "需要与当前项目、Run 和材料完全匹配的有效批准")
    return approval


async def apply_configuration(session: AsyncSession, project: Project, run: AgentRun, state: ImplementationGraphState) -> dict:
    await require_receipt(session, project, run, state, "configuration")
    ws = await workspace(session, project)
    before = deepcopy(ws.configuration)
    after = deepcopy(before)
    for change in state.configuration_changes:
        if change.path not in {"workflow.statuses", "notifications.due_date", "departments", "templates", "custom_fields"}:
            raise HTTPException(422, "配置路径不在工具允许范围")
        if before.get(change.path) not in (change.old_value, change.new_value):
            raise HTTPException(409, "批准后的配置已变化，请重新制定方案")
        after[change.path] = change.new_value
    validate_configuration(after)
    ws.configuration = after
    ws.version += int(before != after)
    await session.flush()
    await session.refresh(ws)
    verified = ws.configuration == after
    if not verified:
        raise HTTPException(409, "配置回读验证失败")
    checksum = digest([c.model_dump() for c in state.configuration_changes])
    receipt = await session.scalar(select(ToolExecution).where(ToolExecution.project_id == project.id, ToolExecution.kind == "configuration", ToolExecution.material_hash == checksum))
    result = {"verified": verified, "configuration": after, "version": ws.version}
    if not receipt:
        session.add(ToolExecution(tenant_id=project.tenant_id, project_id=project.id, run_id=run.id, kind="configuration", material_hash=checksum, before=before, result=result))
    session.add(AuditEvent(tenant_id=project.tenant_id, event_type="tenant.configuration.applied", actor="agent", resource_id=project.id, payload=result))
    return result


def validate_configuration(configuration: dict) -> None:
    for key in ("departments", "templates", "workflow.statuses", "custom_fields"):
        items = configuration.get(key)
        if not isinstance(items, list) or len(items) > 50 or any(not isinstance(v, str) or not v.strip() or len(v) > 120 for v in items) or len(set(items)) != len(items):
            raise HTTPException(422, "部门、模板、字段和状态必须为不重复的有效名称列表")
        if key != "custom_fields" and not items:
            raise HTTPException(422, "部门、模板和状态不能为空")
    if not isinstance(configuration.get("notifications.due_date"), bool):
        raise HTTPException(422, "到期提醒必须为布尔值")


async def execute_csv(session: AsyncSession, project: Project, run: AgentRun, state: ImplementationGraphState, job: ImportJob, approval_id: str | None = None) -> dict:
    if job.run_id != run.id or str(state.import_job_id) != job.id:
        raise HTTPException(403, "CSV 与当前 Run 的批准材料不匹配")
    await require_receipt(session, project, run, state, "import", approval_id)
    if not job.validation.get("valid") or hashlib.sha256(job.csv_text.encode()).hexdigest() != job.source_hash:
        raise HTTPException(409, "CSV 无效或批准后内容被修改")
    ws = await workspace(session, project)
    existing = {m.email: m for m in (await session.scalars(select(SaaSMember).where(SaaSMember.workspace_id == ws.id))).all()}
    rows = []
    for number, row in enumerate(csv_rows(job), 2):
        member = existing.get(row["email"])
        error = None
        if row["department"] not in ws.configuration["departments"] or row["role"] not in ROLES:
            error = "目标部门或角色已变化"
        elif member and any(getattr(member, key) != value for key, value in row.items()):
            error = "目标成员资料冲突"
        if error:
            rows.append({"row": number, "email": row["email"], "status": "failed", "error": error})
            continue
        if member is None:
            member = SaaSMember(tenant_id=project.tenant_id, workspace_id=ws.id, **row)
            session.add(member)
            existing[row["email"]] = member
        rows.append({"row": number, "email": row["email"], "status": "completed"})
    await session.flush()
    persisted = {m.email: m for m in (await session.scalars(select(SaaSMember).where(SaaSMember.workspace_id == ws.id))).all()}
    successful = sum(row["status"] == "completed" for row in rows)
    verified = all(row["email"] in persisted for row in rows if row["status"] == "completed")
    result = {"successful": successful, "failed": len(rows) - successful, "verified": verified, "rows": rows}
    job.status = "completed" if successful == len(rows) and verified else "partial_failed"
    job.result = result
    session.add(AuditEvent(tenant_id=project.tenant_id, event_type="import.executed", actor="agent", resource_id=job.id, payload=result))
    return result


async def training(session: AsyncSession, project: Project, run: AgentRun) -> dict:
    ws = await workspace(session, project)
    contents = {
        "admin_guide": ("管理员快速入门", "核对部门与角色，按最小权限分配成员；配置变更先预览再由独立审批人批准。导入前核对 CSV，完成后查看失败行与实际成员数。"),
        "member_guide": ("成员操作指南", "确认自己的部门与角色；使用项目模板创建业务记录，按状态流推进；遇到访问不足请联系管理员，不共享账号。"),
        "faq": ("常见问题 FAQ", "导入失败怎么办？下载失败行并修正后重试。\n为什么不能审批？发起人不能审批自己的请求。\n项目为何无法关闭？先处理验收报告里的阻塞项，再重新检查。"),
    }
    ids = []
    for kind, (title, body) in contents.items():
        text = f"# {project.customer_name} · {title}\n\n{body}\n\n## 本项目配置\n\n```json\n{json.dumps(ws.configuration, ensure_ascii=False, indent=2)}\n```\n\n来源：Run {run.id}，配置版本 {ws.version}。"
        item = await artifact(session, project, run, kind, title, text)
        ids.append(item.id)
    return {"artifact_ids": ids}


async def go_live_checks(session: AsyncSession, project: Project, run: AgentRun, state: ImplementationGraphState) -> AcceptanceReport:
    ws = await workspace(session, project)
    doc = await document(session, project)
    members = (await session.scalars(select(SaaSMember).where(SaaSMember.workspace_id == ws.id))).all()
    job = await session.get(ImportJob, str(state.import_job_id)) if state.import_job_id else None
    migration = await migration_required(session, project)
    plan = await session.scalar(select(Approval).where(Approval.run_id == run.id, Approval.kind == "plan", Approval.status == "approved"))
    artifacts = set((await session.scalars(select(ProjectArtifact.kind).where(ProjectArtifact.run_id == run.id))).all())
    checks = [
        GoLiveCheckResult(name="实施计划已审批", passed=bool(plan), details="检查本次 Run 的独立审批记录"),
        GoLiveCheckResult(name="租户配置已验证", passed=all(ws.configuration.get(c.path) == c.new_value for c in state.configuration_changes), details=f"配置版本 {ws.version}"),
        GoLiveCheckResult(name="成员与管理员完整", passed=not migration or (len(members) == doc.get("employee_count") and any(m.role == "admin" for m in members)), details=f"实际 {len(members)} 人；预期 {doc.get('employee_count')} 人" if migration else "本项目无成员迁移"),
        GoLiveCheckResult(name="成员角色与部门有效", passed=all(m.role in ROLES and m.department in ws.configuration["departments"] for m in members), details="逐成员核对目标业务角色及部门"),
        GoLiveCheckResult(name="导入无未处理失败", passed=not migration or bool(job and job.status == "completed" and job.result.get("verified")), details=job.status if job else "无导入任务"),
        GoLiveCheckResult(name="培训材料已生成", passed={"admin_guide", "member_guide", "faq"} <= artifacts, details="核对本次 Run 的三类完整交付物"),
    ]
    blockers = [c.name for c in checks if not c.passed]
    return AcceptanceReport(ready=not blockers, checks=checks, blockers=blockers)


async def sync_remediation(session: AsyncSession, project: Project, run: AgentRun, report: AcceptanceReport) -> None:
    from datetime import UTC, datetime

    from backend.models import RemediationTask
    for check in report.checks:
        item = await session.scalar(select(RemediationTask).where(RemediationTask.project_id == project.id, RemediationTask.check_name == check.name))
        if item is None and not check.passed:
            item = RemediationTask(tenant_id=project.tenant_id, project_id=project.id, run_id=run.id, check_name=check.name)
            session.add(item)
        if item:
            item.status = "resolved" if check.passed else "open"
            item.run_id = run.id
            item.evidence = check.model_dump()
            item.updated_at = datetime.now(UTC)
    await session.flush()
