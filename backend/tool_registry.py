"""One optional-tool contract for implementation, chat and MCP."""
from copy import deepcopy
from dataclasses import dataclass

import httpx
from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from backend.config import get_settings
from backend.models import ImportJob
from backend.observability import span
from backend.tool_types import (
    EmptyArguments,
    PreviewArguments,
    ReadOfficialArguments,
    SearchOfficialArguments,
    ToolResult,
)


@dataclass(frozen=True)
class OptionalTool:
    parameters: type[BaseModel]
    stages: frozenset[str]
    permission: str
    limit: int
    description: str
    external: bool = False


TOOLS = {
    "check_project_brief": OptionalTool(EmptyArguments, frozenset({"discovery"}), "project.view", 2, "检查授权项目需求材料的完整性；缺失事实不能编造"),
    "preview_import": OptionalTool(PreviewArguments, frozenset({"migration"}), "import.validate", 2, "预览授权成员导入任务的错误和影响；不会创建成员或批准导入"),
    "search_official_docs": OptionalTool(SearchOfficialArguments, frozenset({"discovery", "configuration"}), "project.view", 1, "使用登记的公开产品、主题和版本搜索官方资料，不接受客户正文", True),
    "read_official_page": OptionalTool(ReadOfficialArguments, frozenset({"discovery", "configuration"}), "project.view", 2, "读取本轮搜索的官方网页引用，返回可追溯证据", True),
}


async def official_evidence(session, run):
    """Only validated, immutable read outputs may become Run evidence."""
    from backend.run_snapshots import RunSnapshot, verify
    rows = await session.scalars(select(RunSnapshot).where(RunSnapshot.run_id == run.id,
        RunSnapshot.kind == "tool").order_by(RunSnapshot.sequence))
    sources = {}
    for row in rows:
        verify(row, run)
        if row.payload.get("tool") == "read_official_page":
            for source in row.payload.get("sources", []):
                if source.get("kind") == "official_web":
                    sources[source["id"]] = source
    return list(sources.values())


def available(stage=None):
    settings = get_settings()
    return {name: spec for name, spec in TOOLS.items()
            if (spec.external and settings.official_web_enabled or not spec.external and settings.skill_runtime_enabled)
            and (stage is None or stage in spec.stages)}


async def execute(session, project, name, arguments, *, scope: str, run=None) -> dict:
    if name not in available():
        raise HTTPException(403, "工具尚未启用或不在允许范围")
    args = TOOLS[name].parameters.model_validate(arguments)
    from backend.tools.materials import check_brief, preview

    with span("tool.optional", tool=name):
        sources, warnings = [], []
        if name == "check_project_brief":
            from backend.delivery import document
            from backend.run_snapshots import requirements
            draft = deepcopy(await document(session, project, run))
            if run:
                from backend.run_snapshots import inputs
                snapshot = await inputs(session, run)
                identity = snapshot.payload["project"] if snapshot else {"name": project.name, "customer_name": project.customer_name}
                draft["requirements_text"] = await requirements(session, run, project)
            else:
                identity = {"name": project.name, "customer_name": project.customer_name}
                draft["requirements_text"] = project.requirements_text
            for field, value in identity.items():
                draft.setdefault(field, value)
            result = check_brief(draft)
        elif name == "preview_import":
            args = PreviewArguments.model_validate(arguments)
            job_id = args.job_id or (run.state.get("import_job_id") if run else None)
            job = await session.scalar(select(ImportJob).where(ImportJob.id == str(job_id),
                ImportJob.tenant_id == project.tenant_id, ImportJob.project_id == project.id))
            if not job or run and job.run_id != run.id:
                raise HTTPException(404, "没有属于当前项目与执行的导入材料")
            result = await preview(session, project, job.csv_text)
        else:
            from backend.tools.official_web import read, search
            try:
                result = await (search(args, scope) if name == "search_official_docs" else read(args, scope))
            except (httpx.HTTPError, TimeoutError):
                result = {"status": "unavailable", "reason": "官方资料服务暂不可用，外部核验未完成"}
            except HTTPException as exc:
                if exc.status_code != 503:
                    raise
                result = {"status": "unavailable", "reason": exc.detail}
            sources = result.pop("sources", [])
            warnings = result.pop("warnings", [])
        output = ToolResult(tool=name, result=result, sources=sources, warnings=warnings).model_dump(mode="json")
        from backend.run_snapshots import record
        await record("tool", {"scope": scope, **output})
        return output
