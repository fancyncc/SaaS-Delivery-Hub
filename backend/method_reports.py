"""Skill-authored advisory reports; business decisions always stay deterministic."""
from pydantic import BaseModel, ConfigDict, Field

from backend.config import get_settings
from backend.skill_runtime import selected_skill


class ReportSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=120)
    guidance: str = Field(min_length=1, max_length=4000)
    evidence_ids: list[str] = Field(max_length=20)


class MethodReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sections: list[ReportSection] = Field(min_length=1, max_length=12)
    questions: list[str] = Field(max_length=20)


async def write(session, project, run, kind: str, title: str, evidence: dict, sections: list[dict]):
    method = selected_skill.get()
    if not method:
        return None
    report = MethodReport(sections=sections, questions=[])
    if get_settings().model_mode != "deterministic":
        from backend.intelligence import structured
        report = await structured(
            "按当前实施方法生成建议报告和待核实问题。仅引用 evidence 中的 ID；事实必须来自对应证据，"
            "不能改写真实检查结果、授予权限或宣称通过审批。练习的预期结果不是已执行结果",
            {"evidence": evidence, "suggested_structure": report.model_dump()}, MethodReport)
    from fastapi import HTTPException
    if any(not set(section.evidence_ids) <= set(evidence) or not section.evidence_ids for section in report.sections):
        raise HTTPException(422, "实施方法报告引用不匹配，需要人工复核")
    from backend.delivery import artifact
    body = {"advisory": True, "target": "builtin_simulated_saas", "skill": {
        key: method[key] for key in ("name", "version", "hash")},
        "report": report.model_dump(mode="json"), "evidence": evidence,
        "notice": "建议材料；审批、实际执行与验收结论以业务凭证为准"}
    import json
    return await artifact(session, project, run, kind, title, json.dumps(body, ensure_ascii=False, indent=2))
