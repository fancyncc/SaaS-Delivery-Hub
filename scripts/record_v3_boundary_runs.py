"""Measure synthetic V3 cases against real services without publishing a release.

Each format uses a fresh, uncommitted tenant. SQL is rolled back and only search
entries bearing the generated source IDs are removed, including after failure.
This records operational rules-mode behavior; labels still require human review.
"""

import argparse
import asyncio
import hashlib
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from backend.config import get_settings
from backend.db import SessionLocal, engine
from backend.lexical_index import client
from backend.models import KnowledgeDocument, Tenant
from backend.rag_v3 import inspect
from backend.rag_v3_index import index_document, register
from backend.rag_v3_models import V3Node, V3Unit
from backend.rag_v3_release import RELEASE_FORMATS, model_identity
from backend.rag_v3_search import index
from scripts.evaluate_v3_demo import citation_errors, summarize


def contains_fact(text, fact):
    # Values/field names must be whole tokens; 17 must not match 170 or 2017.
    if re.fullmatch(r"[\w, .-]+", fact, re.ASCII):
        return bool(re.search(r"(?<!\w)" + re.escape(fact) + r"(?!\w)", text))
    return fact in text


def annotated_facts(case):
    return list(dict.fromkeys([*case["necessary_facts"], *case.get("protected_conditions", [])]))


def count_facts(case, hits, origin_id):
    keys = case["source_keys"]
    gold = [hit for hit in hits if hit["origin_id"] == origin_id and (
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
            session.add(Tenant(id=tenant_id, name="Uncommitted V3 evaluation", slug="eval-" + tenant_id))
            await session.flush()
            documents = {}
            index_metrics = []
            for filename in dict.fromkeys(case["document"] for case in cases):
                path = (args.cases.parent / filename).resolve()
                if not path.is_relative_to(args.cases.parent.resolve()):
                    raise ValueError("Fixture path must remain within the corpus directory")
                doc = KnowledgeDocument(
                    tenant_id=tenant_id, title=path.stem, version=1, module="evaluation",
                    source=path.name, license="synthetic fixture", body="", index_status="v3_pending",
                )
                session.add(doc)
                await session.flush()
                source = await register(session, doc, path.name, path.read_bytes())
                source_ids.append(source.id)
                await asyncio.wait_for(index_document(session, source), timeout=660)
                documents[source.id] = doc
                index_metrics.append({"document": filename, **source.metrics})
            await session.flush()
            source_units = await session.scalars(select(V3Unit).where(V3Unit.document_id.in_(source_ids)))
            units = {unit.id: {
                "id": unit.id, "document_id": unit.document_id,
                "origin_id": documents[unit.document_id].id,
                "title": documents[unit.document_id].title,
                "source": documents[unit.document_id].source,
                "version": 1, "heading": unit.heading, "kind": unit.kind,
                "node_id": unit.node_id, "location": unit.location, "text": unit.text,
            } for unit in source_units}
            source_nodes = await session.scalars(select(V3Node).where(V3Node.document_id.in_(source_ids)))
            nodes = {node.id: (node.structure.get("context", "") + "\n" if node.structure.get("context") else "")
                     + node.structure["text"] for node in source_nodes}
            await inspect(session, tenant_id, cases[0]["question"], [], args.budget)
            for case in cases:
                origin_id = next(doc.id for doc in documents.values() if doc.source == Path(case["document"]).name)
                expected = len(annotated_facts(case))
                if count_facts(case, list(units.values()), origin_id) != expected:
                    raise ValueError(f"{case['id']}: labelled facts/conditions missing at annotated source key")
                started = time.perf_counter()
                result = await asyncio.wait_for(inspect(session, tenant_id, case["question"], [], args.budget), timeout=180)
                elapsed = round((time.perf_counter() - started) * 1000, 2)
                candidates = result.get("diagnostics", {}).get("candidates", [])
                selected = result["chunks"]
                observed = [*selected, *candidates]
                row = {
                    "id": case["id"], "format": fmt, "language": case["language"],
                    "split": case["split"], "kind": case["kind"], "budget": args.budget,
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
                print(f"{case['id']}: {row['covered_necessary']}/{expected}, {row['status']}", flush=True)
            await session.rollback()
        return rows, index_metrics
    finally:
        await cleanup_search(source_ids)


async def main(args):
    if get_settings().rag_mode != "real" or get_settings().rag_v3_relevance_mode != "rules":
        raise ValueError("Operational recording requires real services and rules mode")
    cases = [json.loads(line) for line in args.cases.read_text(encoding="utf-8").splitlines()]
    selected_cases = [case for case in cases if case["format"] in args.formats]
    if not selected_cases:
        raise ValueError("No cases selected")
    rows, index_metrics = [], []
    try:
        for fmt in args.formats:
            subset = [case for case in selected_cases if case["format"] == fmt]
            if not subset:
                raise ValueError(f"No cases for {fmt}")
            measured, indexed = await record_format(fmt, subset, args)
            rows.extend(measured)
            index_metrics.extend(indexed)
            report = {
                "generated_at": datetime.now(UTC).isoformat(), "release_approved": False,
                "complete": len(rows) == len(selected_cases),
                "scope": "synthetic operational observations; not calibrated locked validation or paired model comparison",
                "independent_review_completed": False, "baseline_measured": False,
                "identity": model_identity(), "relevance_mode": "rules",
                "cases_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
                "test_data_cleaned": True, "metrics": summarize(rows),
                "per_format": {f: summarize([r for r in rows if r["format"] == f]) for f in args.formats if any(r["format"] == f for r in rows)},
                "index_metrics": index_metrics, "rows": rows,
            }
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"completed_format": fmt, "metrics": report["per_format"][fmt]}, ensure_ascii=True), flush=True)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path("evaluations/v3/cases.jsonl"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--formats", nargs="+", choices=RELEASE_FORMATS, default=list(RELEASE_FORMATS))
    parser.add_argument("--budget", type=int, choices=(600, 1200, 1800), default=1200)
    args = parser.parse_args()
    asyncio.run(main(args))
