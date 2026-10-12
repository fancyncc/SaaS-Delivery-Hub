"""Measure synthetic 知识检索 cases against real services without publishing a release.

Each format uses a fresh, uncommitted tenant. SQL is rolled back and only search
entries bearing the generated source IDs are removed, including after failure.
This records operational rules-mode behavior; labels still require human review.
"""

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from backend.config import get_settings
from backend.db import SessionLocal, engine
from backend.evaluation_artifacts import (
    RunJournal,
    implementation_checksums,
    read_rows,
    runtime_config,
    verify_frozen,
)
from backend.evaluation_scoring import contains_fact
from backend.evaluation_scoring import facts as annotated_facts
from backend.knowledge_index import index_document, register
from backend.knowledge_index_models import DocumentNode, RetrievalUnit
from backend.knowledge_retrieval import inspect
from backend.knowledge_search import index
from backend.lexical_index import client
from backend.models import KnowledgeDocument, Tenant
from backend.retrieval_diagnostics import evaluation_trace
from backend.retrieval_policy import PIPELINE_SCHEMA
from backend.retrieval_release import RELEASE_FORMATS, model_identity
from scripts.evaluate_retrieval_demo import citation_errors, summarize


async def evaluation_inspect(session, tenant, question, budget):
    """A candidate classifier is evaluated before it can become a runtime release."""
    from backend.knowledge_retrieval import run
    from backend.retrieval_release import FEATURES

    settings = get_settings()
    if settings.retrieval_relevance_mode == "rules":
        return await inspect(session, tenant, question, [], budget)
    data = json.loads(Path(settings.retrieval_release).read_bytes())
    classifier = data.get("classifier", data)
    import math

    if (set(classifier["weights"]) != set(FEATURES)
            or not 0 <= classifier["threshold"] <= 1
            or not all(type(v) in (int, float) and math.isfinite(v) for v in
                       [classifier["intercept"], classifier["threshold"], *classifier["weights"].values()])):
        raise ValueError("Invalid candidate classifier")
    return await run(session, tenant, question, [], budget,
                     {"relevance_mode": "calibrated", "classifier": classifier})


def count_facts(case, hits, origin_id):
    import re

    keys = case["source_keys"]
    gold = [hit for hit in hits if hit["origin_id"] == origin_id
        and (not case.get("required_page") or hit.get("location", {}).get("page") == case["required_page"])
        and (
        not keys or any(re.search(r"(?<!\w)" + re.escape(key) + r"(?!\w)",
                                 hit["text"], re.I) for key in keys)
    )]
    return sum(any(contains_fact(hit["text"], fact) for hit in gold)
               for fact in annotated_facts(case))


async def cleanup_search(source_ids):
    if not source_ids:
        return
    async with client() as http:
        response = await http.post("/" + index() + "/_delete_by_query?refresh=true", json={
            "query": {"terms": {"document_id": source_ids}},
        })
        response.raise_for_status()
        result = response.json()
        if result.get("failures") or result.get("timed_out"):
            raise RuntimeError("Temporary evaluation search cleanup failed")
        remaining = await http.post("/" + index() + "/_count", json={
            "query": {"terms": {"document_id": source_ids}},
        })
        remaining.raise_for_status()
        if remaining.json()["count"]:
            raise RuntimeError("Temporary evaluation search entries remain")


async def record_format(fmt, cases, args):
    source_ids, rows = [], []
    tenant_id = str(uuid4())
    try:
        async with SessionLocal() as session:
            session.info["rls_context"] = {
                "app.current_tenant_id": tenant_id, "app.current_company_role": "company_admin",
            }
            session.add(Tenant(id=tenant_id, name="Uncommitted 知识检索 evaluation", slug="eval-" + tenant_id))
            await session.flush()
            documents, by_filename = {}, {}
            index_metrics = []
            for filename in dict.fromkeys(case["document"] for case in cases):
                path = (args.cases.parent / filename).resolve()
                if not path.is_relative_to(args.cases.parent.resolve()):
                    raise ValueError("Fixture path must remain within the corpus directory")
                doc = KnowledgeDocument(
                    tenant_id=tenant_id, title=path.stem, version=1, module="evaluation",
                    source=path.name, license=getattr(args, "source_license", "synthetic fixture"), body="", index_status="indexed_pending",
                )
                session.add(doc)
                await session.flush()
                source = await register(session, doc, path.name, path.read_bytes())
                source_ids.append(source.id)
                await asyncio.wait_for(index_document(session, source), timeout=660)
                documents[source.id] = doc
                by_filename[filename] = (source, doc)
                index_metrics.append({"document": filename, **source.metrics})
            await session.flush()
            source_units = await session.scalars(select(RetrievalUnit).where(RetrievalUnit.document_id.in_(source_ids)))
            units = {unit.id: {
                "id": unit.id, "document_id": unit.document_id,
                "origin_id": documents[unit.document_id].id,
                "title": documents[unit.document_id].title,
                "source": documents[unit.document_id].source,
                "version": 1, "heading": unit.heading, "kind": unit.kind,
                "node_id": unit.node_id, "location": unit.location, "text": unit.text,
            } for unit in source_units}
            source_nodes = await session.scalars(select(DocumentNode).where(DocumentNode.document_id.in_(source_ids)))
            nodes = {node.id: (node.structure.get("context", "") + "\n" if node.structure.get("context") else "")
                     + node.structure["text"] for node in source_nodes}
            snapshot = getattr(args, "source_snapshot", None)
            if snapshot is not None:
                snapshot["units"].update(units)
                snapshot["nodes"].update(nodes)
                snapshot["documents"].update({source.id: {
                    "document": filename, "origin_id": doc.id,
                    "sha256": hashlib.sha256((args.cases.parent / filename).read_bytes()).hexdigest(),
                } for filename, (source, doc) in by_filename.items()})
            await asyncio.wait_for(evaluation_inspect(session, tenant_id, cases[0]["question"], args.budget), timeout=180)
            for case in cases:
                origin_id = by_filename[case["document"]][1].id
                expected = len(annotated_facts(case))
                started = time.perf_counter()
                try:
                    with evaluation_trace():
                        result = await asyncio.wait_for(evaluation_inspect(session, tenant_id, case["question"], args.budget), timeout=180)
                except Exception as exc:
                    row = {"id": case["id"], "outcome": "timeout" if isinstance(exc, TimeoutError) else "failed",
                           "error_type": type(exc).__name__}
                    rows.append(row)
                    if getattr(args, "journal", None):
                        args.journal.append(row)
                    continue
                elapsed = round((time.perf_counter() - started) * 1000, 2)
                candidates = result.get("diagnostics", {}).get("candidates", [])
                selected = result["chunks"]
                observed = [*selected, *candidates]
                row = {
                    "id": case["id"], "format": case["format"], "language": case["language"],
                    "split": case["split"], "kind": case["kind"], "budget": args.budget,
                    "pipeline_schema": PIPELINE_SCHEMA, "outcome": "succeeded",
                    "necessary_total": expected,
                    "recalled_necessary": count_facts(case, candidates, origin_id),
                    "covered_necessary": count_facts(case, selected, origin_id),
                    "returned_evidence": bool(selected), "status": result["status"],
                    "permission_leaks": sum(hit["document_id"] not in documents for hit in observed),
                    "citation_errors": citation_errors(observed, units, nodes),
                    "final_tokens": result.get("diagnostics", {}).get("final_tokens", 0),
                    "warm_latency_ms": elapsed, "result": result,
                }
                rows.append(row)
                if getattr(args, "journal", None):
                    args.journal.append(row)
                print(f"{case['id']}: {row['covered_necessary']}/{expected}, {row['status']}", flush=True)
            await session.rollback()
        return rows, index_metrics
    finally:
        try:
            await cleanup_search(source_ids)
            args.cleanup_verified = True
        except BaseException:
            args.cleanup_verified = False
            raise


async def main(args):
    frozen = json.loads(args.freeze.read_bytes())
    journal = args.journal = RunJournal(args.output, frozen)
    args.source_snapshot = {"documents": {}, "units": {}, "nodes": {}}
    rows, index_metrics, errors = [], [], []
    cleanup, status = False, "failed"
    try:
        verify_frozen(frozen, args.cases, config=runtime_config(args.budget))
        if get_settings().rag_mode != "real":
            raise ValueError("Requires real retrieval services")
        cases = read_rows(args.cases)
        selected_cases = [case for case in cases if case["format"] in args.formats]
        if not selected_cases:
            raise ValueError("No cases selected")
        for fmt in args.formats:
            subset = [case for case in selected_cases if case["format"] == fmt]
            if not subset:
                continue
            measured, indexed = await record_format(fmt, subset, args)
            rows.extend(measured)
            index_metrics.extend(indexed)
        cleanup = True
        verify_frozen(frozen, args.cases, config=runtime_config(args.budget))
        status = "succeeded" if all(r["outcome"] == "succeeded" for r in rows) else "failed"
    except BaseException as exc:
        errors.append(type(exc).__name__)
        status = "interrupted" if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)) else "failed"
        cleanup = getattr(args, "cleanup_verified", False)
    finally:
        await engine.dispose()
        successful = [r for r in journal.rows if r.get("outcome") == "succeeded"]
        report = journal.finish(status, cleanup=cleanup, errors=errors,
            pipeline_schema=PIPELINE_SCHEMA, identity=model_identity(),
            cases_sha256=frozen["corpus"]["cases_sha256"], implementation_sha256=implementation_checksums(),
            metrics=summarize(successful), index_metrics=index_metrics, source_snapshot=args.source_snapshot)
    print(json.dumps({"run_id": journal.run_id, "status": report["status"],
                      "complete": report["complete"], "metrics": report["metrics"]}), flush=True)
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path("evaluations/retrieval/cases.jsonl"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--formats", nargs="+", choices=RELEASE_FORMATS, default=list(RELEASE_FORMATS))
    parser.add_argument("--budget", type=int, choices=(600, 1200, 1800), default=1200)
    args = parser.parse_args()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(asyncio.run(main(args)))
