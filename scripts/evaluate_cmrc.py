"""Prepare, run and score one public CMRC 2018 benchmark using application services."""

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.cmrc_evaluation import REVISION, SOURCE_SHA256, load_corpus, prepare, score_run
from backend.evaluation_artifacts import (
    RunJournal,
    canonical,
    digest,
    environment,
    implementation_checksums,
    runtime_config,
    write_once,
)

SHORT_ANSWER_INSTRUCTION = (
    "仅根据所提供的资料回答下面的问题。答案只写最短且完整的答案短语，"
    "尽量使用原文，不复述问题，不加解释或前缀。保留来源引用编号。"
    "资料不足时回答‘资料不足’，不要使用外部知识。\n问题："
)


async def run(args):
    from sqlalchemy import select

    from backend.chat import answer_question
    from backend.config import get_settings
    from backend.db import SessionLocal, engine
    from backend.knowledge_index import index_document, register
    from backend.knowledge_retrieval import inspect
    from backend.model_observations import capture_model_receipts, measured_usage
    from backend.models import KnowledgeDocument, Tenant
    from backend.retrieval_diagnostics import evaluation_trace
    from scripts.record_retrieval_boundary_runs import cleanup_search

    cases, manifest = load_corpus(args.corpus)
    selected = cases[:args.max_cases] if args.max_cases else cases
    settings = get_settings()
    if settings.rag_mode != "real" or settings.model_mode != "real":
        raise ValueError("CMRC run requires real retrieval and generation; mock results are not scored")
    before = implementation_checksums()
    frozen = {"schema": 1, "manifest_sha256": digest(canonical(manifest)),
              "implementation_sha256": before, "config": runtime_config(args.budget),
              "environment": environment(),
              "case_ids": [c["id"] for c in selected], "short_answer_instruction": SHORT_ANSWER_INSTRUCTION,
              "corpus": {"case_ids": [c["id"] for c in selected]},
              "corpus_scope": manifest["corpus_scope"],
              "started_at": datetime.now(UTC).isoformat()}
    journal = RunJournal(args.output, frozen)
    source_ids, errors, index_metrics = [], [], []
    snapshot = {"documents": {}}
    cleanup, status = False, "failed"
    tenant_id = str(uuid4())
    try:
        async with SessionLocal() as session:
            session.info["rls_context"] = {"app.current_tenant_id": tenant_id,
                                           "app.current_company_role": "company_admin"}
            session.add(Tenant(id=tenant_id, name="Temporary CMRC benchmark", slug="cmrc-" + tenant_id))
            await session.flush()
            # Every paragraph in the prepared corpus is a distractor for other questions.
            # Gold paragraph restriction is never passed to the retrieval function.
            for ordinal, filename in enumerate(manifest["sources_sha256"], 1):
                path = args.corpus / filename
                title = next(c["title"] for c in cases if c["document"] == filename)
                doc = KnowledgeDocument(tenant_id=tenant_id, title=title[:150] + " · " + path.stem[:12], version=1,
                                        module="evaluation", source=path.name, license="CC BY-SA 4.0",
                                        body="", index_status="indexed_pending")
                session.add(doc)
                await session.flush()
                source = await register(session, doc, path.name, path.read_bytes())
                source_ids.append(source.id)
                await asyncio.wait_for(index_document(session, source), timeout=660)
                if source.phase != "ready":
                    raise ValueError("CMRC document index not ready")
                snapshot["documents"][source.id] = {"document": filename, "origin_id": doc.id,
                                                    "sha256": source.digest}
                index_metrics.append({"document": filename, **source.metrics})
                if ordinal % 10 == 0 or ordinal == len(manifest["sources_sha256"]):
                    print(f"indexed {ordinal}/{len(manifest['sources_sha256'])}", flush=True)
            await session.flush()
            started = time.perf_counter()
            await asyncio.wait_for(inspect(session, tenant_id, selected[0]["question"], [], args.budget), timeout=180)
            warmup_ms = round((time.perf_counter() - started) * 1000, 2)
            for ordinal, case in enumerate(selected, 1):
                started = time.perf_counter()
                row = {"id": case["id"], "question": case["question"]}
                receipts = []
                try:
                    with evaluation_trace():
                        retrieval = await asyncio.wait_for(inspect(session, tenant_id, case["question"], [], args.budget), timeout=180)
                    row.update(retrieval=retrieval, retrieval_ms=round((time.perf_counter() - started) * 1000, 2))
                    generated_at = time.perf_counter()
                    sources = [dict(h, kind="customer_document") for h in retrieval["chunks"]]
                    with capture_model_receipts() as receipts:
                        reply = await asyncio.wait_for(answer_question(SHORT_ANSWER_INSTRUCTION + case["question"], [], "", sources),
                                                       timeout=max(180, settings.model_timeout * 8))
                    row.update(outcome="succeeded", answer=reply["answer"],
                               citations=[{"id": h["id"], "number": h["number"]} for h in reply["citations"]],
                               generation_ms=round((time.perf_counter() - generated_at) * 1000, 2))
                except Exception as exc:
                    row.update(outcome="timeout" if isinstance(exc, TimeoutError) else "failed",
                               error_type=type(exc).__name__)
                row.update(latency_ms=round((time.perf_counter() - started) * 1000, 2),
                           usage=measured_usage(receipts), model_receipts=receipts)
                journal.append(row)
                print(f"question {ordinal}/{len(selected)} {case['id']}: {row['outcome']}", flush=True)
            await session.rollback()
        # Verify rollback actually removed this tenant's SQL state.
        async with SessionLocal() as session:
            session.info["rls_context"] = {"app.current_tenant_id": tenant_id, "app.current_company_role": "company_admin"}
            if await session.scalar(select(Tenant.id).where(Tenant.id == tenant_id)):
                raise RuntimeError("Temporary CMRC tenant remains after rollback")
        status = "succeeded" if all(r["outcome"] == "succeeded" for r in journal.rows) else "failed"
    except BaseException as exc:
        errors.append(type(exc).__name__)
        status = "interrupted" if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)) else "failed"
        warmup_ms = None
    finally:
        try:
            await cleanup_search(source_ids)
            cleanup = True
        except BaseException as exc:
            errors.append("search_cleanup:" + type(exc).__name__)
            status = "failed"
        try:
            load_corpus(args.corpus)
            if before != implementation_checksums() or frozen["config"] != runtime_config(args.budget):
                raise ValueError("Implementation or runtime configuration changed")
        except Exception as exc:
            errors.append("input_verification:" + type(exc).__name__)
            status = "failed"
        await engine.dispose()
        report = journal.finish(status, cleanup=cleanup, errors=errors,
                                scope="public_cmrc2018_benchmark", source_snapshot=snapshot,
                                index_metrics=index_metrics, warmup_ms=warmup_ms,
                                production_acceptance=False)
    print(json.dumps({"run_id": journal.run_id, "status": status,
                      "complete": report["complete"], "test_data_cleaned": cleanup}), flush=True)
    return 0 if report["complete"] else 1


def render_markdown(report):
    m, usage, cost = report["metrics"], report["usage"], report["cost"]
    lines = ["# CMRC 2018 当前系统评测", "", f"运行：`{report['run_id']}`；状态：`{report['status']}`；完整：`{report['complete']}`。",
             "", f"模型：`{report['frozen']['config']['model_name']}`；Embedding：`{report['frozen']['config']['identity']['embedding']}`；重排模型：`{report['frozen']['config']['identity']['reranker']}`。",
             "", f"使用公开开发集固定样本 {report['question_count']} 题，知识库包含 {report['dataset']['document_count']} 段原文。",
             "题目对应原文共同组成检索库；未将标准答案或原标注位置提供给模型。",
             "", "| 指标 | 结果 |", "|---|---:|", f"| 答案 EM | {m['em']:.3f}% |",
             f"| 答案 F1 | {m['f1']:.3f}% |", f"| 请求错误率 | {report['error_rate']:.3%} |"]
    for k, value in m["answer_evidence_hit_at_k"].items():
        lines.append(f"| 重排后答案证据命中率@{k} | {value:.3%} |" if value is not None else f"| 答案证据命中率@{k} | 未测量 |")
    selected = m["selected_answer_evidence_hit_rate"]
    lines.append(f"| 最终证据答案位置命中率 | {selected:.3%} |" if selected is not None else "| 最终证据答案位置命中率 | 未测量 |")
    for phase, name in (("retrieval_ms", "检索"), ("generation_ms", "生成"), ("latency_ms", "端到端")):
        p95 = report["latency"][phase]["p95_ms"]
        lines.append(f"| {name} P95 | {p95:.2f} ms |" if p95 is not None else f"| {name} P95 | 未测量 |")
    lines.extend(["", f"Token：输入 {usage['input_tokens']}，输出 {usage['output_tokens']}；提供计量的请求 {usage['measured_questions']}/{report['question_count']}。",
                  f"费用：{cost['value']} {cost['currency']}。" if cost["value"] is not None else "费用：未测量；未提供明确单价，不推算费用。",
                  "", f"证据指标分母：{m['evidence_eligible_questions']} 题；{m['evidence_excluded_invalid_positions']} 题因原标注位置无效而排除。答案 EM/F1 包含全部冻结题目，失败计零分。",
                  "", "EM/F1 按 CMRC v6 的标点规则和最长连续混合分词匹配计算，取各原标准答案的最高分。模型输出仅删除数字引用编号。",
                  "答案证据命中要求命中正确原文中的标注字符位置；它不代表完整证据召回率或引用语义正确率。",
                  "", "这是 CMRC 公开开发集改造的 RAG 指标检测，不与原始给定段落阅读理解排行榜直接比较。公开题可能已用于模型训练。",
                  "CMRC 不覆盖无答案、租户权限及全部文档格式。本报告不签发生产验收。",
                  "", f"资料许可：CC BY-SA 4.0；{report['dataset']['attribution']}。", f"数据来源：{report['dataset']['source_url']}",
                  "", f"测试资料清理：{report['test_data_cleaned']}；运行错误：{report['errors']}。", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run", "score"))
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--revision", default=REVISION)
    parser.add_argument("--limit", type=int, default=200, help="prepare sample size; 0 = all public dev questions")
    parser.add_argument("--seed", type=int, default=2018)
    parser.add_argument("--budget", type=int, choices=(600, 1200, 1800), default=1200)
    parser.add_argument("--max-cases", type=int, default=0, help="smoke run only; corpus stays unchanged")
    parser.add_argument("--runs", type=Path)
    parser.add_argument("--input-per-million", type=float)
    parser.add_argument("--output-per-million", type=float)
    parser.add_argument("--currency")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; use a new path")
    if args.max_cases < 0:
        parser.error("max-cases must be nonnegative")
    if args.action == "prepare":
        import httpx

        raw = args.source.read_bytes() if args.source else httpx.get(
            f"https://raw.githubusercontent.com/ymcui/cmrc2018/{args.revision}/squad-style-data/cmrc2018_dev.json",
            timeout=60, follow_redirects=True).raise_for_status().content
        if args.revision == REVISION and digest(raw) != SOURCE_SHA256:
            raise ValueError("Official pinned CMRC dev checksum mismatch")
        manifest = prepare(raw, args.output, limit=args.limit, seed=args.seed, revision=args.revision)
        print(json.dumps({k: manifest[k] for k in ("dataset", "question_count", "document_count", "cases_sha256",
                                                  "invalid_answer_annotations", "questions_without_valid_position")}, ensure_ascii=False))
        return 0
    if not args.corpus:
        parser.error("corpus is required")
    args.corpus = args.corpus.resolve()
    if args.action == "run":
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        return asyncio.run(run(args))
    if not args.runs:
        parser.error("runs is required for scoring")
    report = score_run(args.corpus, json.loads(args.runs.read_bytes()),
                       input_price=args.input_per_million, output_price=args.output_per_million,
                       currency=args.currency)
    write_once(args.output, report)
    args.output.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    predictions = {r["id"]: r["prediction"] for r in report["rows"]}
    write_once(args.output.with_name(args.output.stem + "-predictions.json"), predictions)
    print(json.dumps({"complete": report["complete"], "metrics": report["metrics"], "usage": report["usage"]}, ensure_ascii=False))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
