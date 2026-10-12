"""Deterministic draft checks and side-effect-free member import preview."""
from __future__ import annotations

import json
import re

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from backend.delivery import csv_rows, validate_target_rows
from backend.imports import validate_member_csv
from backend.models import ImportJob, SaaSMember, SaaSWorkspace
from backend.schemas import ProjectCreate

REQUIRED = ("name", "customer_name", "customer_contact", "contact_email", "employee_count",
            "target_go_live_date", "departments", "requirements_text")


def check_brief(draft: dict) -> dict:
    if len(json.dumps(draft, ensure_ascii=False).encode()) > 64000:
        raise HTTPException(413, "项目草稿超过大小限制")
    normalized = dict(draft)
    unknown = {"", "待补充", "未知", "未提供"}
    missing = [key for key in REQUIRED if normalized.get(key) is None or
               (isinstance(normalized.get(key), str) and normalized[key].strip() in unknown) or
               normalized.get(key) == []]
    departments = normalized.get("departments")
    if isinstance(departments, list) and all(isinstance(d, str) for d in departments):
        normalized["departments"] = list(dict.fromkeys(d.strip() for d in departments if d.strip()))
        if not normalized["departments"] and "departments" not in missing:
            missing.append("departments")
    issues = []
    submitted = None
    try:
        submitted = ProjectCreate.model_validate(normalized).model_dump(mode="json")
    except ValidationError as exc:
        issues = [{"field": ".".join(map(str, e["loc"])), "code": e["type"], "message": e["msg"]} for e in exc.errors()]
    # Only compare explicitly labelled facts; general prose and dates remain unknown.
    text = normalized.get("requirements_text", "")
    if isinstance(text, str) and isinstance(normalized.get("employee_count"), int):
        counts = re.findall(r"(?:员工|成员)(?:人数|总数|规模|数量)?\s*[：:=为共]?\s*(\d+)\s*(?:人|名)", text)
        if any(int(count) != normalized["employee_count"] for count in counts):
            issues.append({"field": "employee_count", "code": "fact_conflict", "message": "成员人数与需求中的明确人数不一致，请确认原文"})
    return {"complete": not missing and not issues, "missing_fields": missing, "issues": issues,
            "questions": [f"请补充 {key}" for key in missing], "draft": normalized,
            "submission": submitted if not missing and not issues else None}


async def preview(session, project, csv_text: str) -> dict:
    if len(csv_text.encode()) > 2_000_000 or csv_text.count("\n") > 10000:
        raise HTTPException(413, "CSV 超过预览大小或行数限制")
    result = validate_member_csv(csv_text).model_dump(mode="json")
    workspace = await session.scalar(select(SaaSWorkspace).where(SaaSWorkspace.project_id == project.id,
                                                                 SaaSWorkspace.tenant_id == project.tenant_id))
    if not workspace:
        return {**result, "target_status": "unavailable", "can_execute": False,
                "reason": "目标工作区尚未初始化，请先完成配置阶段"}
    members = list(await session.scalars(select(SaaSMember).where(SaaSMember.workspace_id == workspace.id,
                                                                  SaaSMember.tenant_id == project.tenant_id)))
    if result["valid"]:
        candidate = ImportJob(csv_text=csv_text, validation=result)
        rows = csv_rows(candidate)
        result = validate_target_rows(result, rows, workspace.configuration, members)
    else:
        rows = []
    existing = {m.email: m for m in members}
    error_rows = {e["row"] for e in result["errors"]}
    eligible = [r for number, r in enumerate(rows, 2) if number not in error_rows]
    return {**result, "target_status": "ready", "target_version": workspace.version,
            "can_execute": False, "new_count": sum(r["email"] not in existing for r in eligible),
            "existing_count": sum(r["email"] in existing for r in eligible),
            "valid_row_count": len(eligible), "error_row_count": len(error_rows),
            "conflict_count": sum(e["message"] == "目标成员资料冲突" for e in result["errors"]),
            "notice": "仅预览；正式导入需要有效审批，并重新校验目标状态"}
