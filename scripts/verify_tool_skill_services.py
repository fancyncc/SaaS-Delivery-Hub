"""Small opt-in live probes; no project data, business writes or secret output."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import get_settings
from backend.method_reports import MethodReport
from backend.skill_runtime import catalog, selected_skill
from backend.tool_types import ReadOfficialArguments, SearchOfficialArguments


async def capture(check):
    try:
        async with asyncio.timeout(45):
            return await check()
    except Exception as exc:
        reasons = {"模型服务认证失败": "MODEL_AUTHENTICATION_FAILED",
                   "模型服务暂不可用": "MODEL_UNAVAILABLE",
                   "模型输出未通过结构化校验，需要人工处理": "MODEL_INVALID_OUTPUT"}
        detail = getattr(exc, "detail", "")
        reason = reasons.get(detail, "SERVICE_OR_CONFIGURATION_ERROR") if isinstance(detail, str) else "SERVICE_ERROR"
        return {"status": "failed", "error_type": type(exc).__name__,
                "reason": reason,
                "http_status": getattr(getattr(exc, "response", None), "status_code", getattr(exc, "status_code", None))}


async def model_probe():
    settings = get_settings()
    if settings.model_mode != "real":
        return {"status": "not_configured", "reason": "MODEL_MODE_real_required"}
    from backend.intelligence import structured
    token = selected_skill.set(catalog()["create-implementation-project-document"])
    try:
        report = await structured("指出缺失字段并保留未知；只引用 brief，不能编造人数和日期或宣称已上线", {
            "evidence": {"brief": {"customer_contact": None, "employee_count": None, "target_go_live_date": None}}}, MethodReport)
        valid = all(section.evidence_ids and set(section.evidence_ids) <= {"brief"} for section in report.sections)
        return {"status": "passed" if valid else "failed", "check": "structured_output_and_citation_ids",
                "sections": len(report.sections), "quality_review_required": True}
    finally:
        selected_skill.reset(token)


async def web_probe(args):
    settings = get_settings()
    if not settings.official_web_enabled or not settings.official_search_api_key or not settings.official_products:
        return {"status": "not_configured", "reason": "official_web_flag_provider_and_catalog_required"}
    from backend.tools.official_web import read, search
    product = args.product or next(iter(settings.official_products))
    topics = settings.official_products.get(product, {}).get("topics", {})
    topic = args.topic or next(iter(topics), "")
    found = await search(SearchOfficialArguments(product=product, topic=topic, version=args.version), "acceptance-probe")
    if not found["results"]:
        return {"status": "failed", "reason": "no_official_results"}
    result = await read(ReadOfficialArguments(reference=found["results"][0]["reference"]), "acceptance-probe")
    sources = result.get("sources", [])
    return {"status": "passed" if sources else "failed", "provider": "brave", "search_results": len(found["results"]),
            "sources": [{key: source[key] for key in ("url", "content_hash", "retrieved_at", "version_verified", "published_at")}
                        for source in sources], "warnings": result.get("warnings", [])}


async def main(args):
    methods = catalog()
    report = {"created_at": datetime.now(UTC).isoformat(), "production_accepted": False,
              "skills": {name: {key: value[key] for key in ("version", "hash")} for name, value in methods.items()},
              "runtime_enabled": get_settings().skill_runtime_enabled}
    if args.exercise:
        report["live_model"] = await capture(model_probe)
        report["live_official_web"] = await capture(lambda: web_probe(args))
    else:
        report["live_model"] = report["live_official_web"] = {"status": "not_exercised"}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exercise", action="store_true", help="Make one small model call and one public search/read")
    parser.add_argument("--product", default="")
    parser.add_argument("--topic", default="")
    parser.add_argument("--version", default="")
    parser.add_argument("--output", default=str(ROOT / "tests/artifacts/tool_skill_services.json"))
    asyncio.run(main(parser.parse_args()))
