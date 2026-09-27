"""Record deployed V3 evidence on authored demo labels; never authorize a release.

Run inside the API container with an explicit demo manifest and output path.
SQL is read-only. No thresholds, labels, service settings or indexes are changed.
"""

import argparse
import asyncio
import hashlib
import json
import math
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text

from backend.db import SessionLocal, engine
from backend.rag_v3 import inspect, visible_documents
from backend.rag_v3_models import V3Node, V3Unit
from backend.rag_v3_release import model_identity
from evaluations.inspection_cases import CASES


def span_coverage(spans, hits, origin_id):
    """Credit entire statements only in the annotated source document."""
    return sum(
        any(span in h["text"] and h["origin_id"] == origin_id for h in hits)
        for span in spans
    )


def citation_errors(hits, units, nodes):
    errors = []
    for hit in hits:
        refs = hit.get("source_chunk_ids", [hit["id"]])
        originals = [units.get(ref) for ref in refs]
        if not refs or any(item is None for item in originals):
            errors.append({"id": hit["id"], "reason": "unknown_source_unit"})
            continue
        first = originals[0]
        keys = ("document_id", "origin_id", "title", "version", "heading", "kind", "node_id", "source")
        if any(hit.get(key) != item.get(key) for item in originals for key in keys):
            errors.append({"id": hit["id"], "reason": "source_metadata_mismatch"})
        original_location = first["location"]
        if any(hit.get("location", {}).get(k) != v for k, v in original_location.items()):
            errors.append({"id": hit["id"], "reason": "source_position_mismatch"})
        expected = (
            nodes.get(first["node_id"])
            if hit.get("location", {}).get("reassembled")
            else first["text"]
        )
        if expected is None or hit["text"] != expected:
            errors.append({"id": hit["id"], "reason": "source_text_changed"})
    return errors


def summarize(rows):
    answerable = [row for row in rows if row["necessary_total"]]
    noanswer = [row for row in rows if not row["necessary_total"]]
    facts = sum(row["necessary_total"] for row in answerable)
    latencies = sorted(row["warm_latency_ms"] for row in rows)
    return {
        "observations": len(rows),
        "required_source_spans": facts,
        "candidate_span_recall": sum(row["recalled_necessary"] for row in answerable) / max(1, facts),
        "selected_span_coverage": sum(row["covered_necessary"] for row in answerable) / max(1, facts),
        "noanswer_cases": len(noanswer),
        "noanswer_returned_evidence": sum(row["returned_evidence"] for row in noanswer),
        "permission_leaks": sum(row["permission_leaks"] for row in rows),
        "citation_errors": sum(len(row["citation_errors"]) for row in rows),
        "budget_violations": sum(row["final_tokens"] > row["budget"] for row in rows),
        "warm_latency_p95_ms": latencies[max(0, math.ceil(len(latencies) * .95) - 1)] if rows else None,
        "statuses": dict(Counter(row["status"] for row in rows)),
    }


async def measure(manifest, case, budget):
    project_ids = [] if case["scope"] == "company_only" else [manifest["project_id"]]
    expected_id = next(
        (doc["id"] for doc in manifest["documents"]
         if Path(doc["file"].replace("\\", "/")).name.startswith(case["document_prefix"] or "NOT_FOUND")),
        None,
    )
    async with SessionLocal() as session:
        session.info["rls_context"] = {
            "app.current_tenant_id": manifest["tenant_id"],
            "app.current_company_role": "company_admin",
        }
        await session.execute(text("SET TRANSACTION READ ONLY"))
        visible = (await session.execute(visible_documents(manifest["tenant_id"], project_ids))).all()
        documents = {source.id: doc for source, doc in visible}
        source_units = list(await session.scalars(select(V3Unit).where(V3Unit.document_id.in_(documents))))
        units = {
            unit.id: {
                "document_id": unit.document_id,
                "origin_id": documents[unit.document_id].id,
                "title": documents[unit.document_id].title,
                "version": documents[unit.document_id].version,
                "source": documents[unit.document_id].source,
                "heading": unit.heading, "kind": unit.kind, "node_id": unit.node_id,
                "location": unit.location, "text": unit.text,
            } for unit in source_units
        }
        if case["required_spans"] and span_coverage(case["required_spans"], list(units.values()), expected_id) != len(case["required_spans"]):
            raise ValueError(f"{case['id']}: annotated spans missing from indexed source; review labels/index first")
        source_nodes = await session.scalars(select(V3Node).where(V3Node.document_id.in_(documents)))
        nodes = {
            node.id: (node.structure.get("context", "") + "\n" if node.structure.get("context") else "") + node.structure["text"]
            for node in source_nodes
        }
        started = time.perf_counter()
        result = await asyncio.wait_for(
            inspect(session, manifest["tenant_id"], case["question"], project_ids, budget),
            timeout=180,
        )
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        candidates = result.get("diagnostics", {}).get("candidates", [])
        selected = result["chunks"]
        observed = [*selected, *candidates]
        final_tokens = result.get("diagnostics", {}).get("final_tokens", 0)
        return {
            "id": case["id"], "split": case["split"], "budget": budget,
            "question": case["question"], "scope": case["scope"],
            "expected_origin_id": expected_id, "required_spans": case["required_spans"],
            "necessary_total": len(case["required_spans"]),
            "recalled_necessary": span_coverage(case["required_spans"], candidates, expected_id),
            "covered_necessary": span_coverage(case["required_spans"], selected, expected_id),
            "returned_evidence": bool(selected), "status": result["status"],
            "permission_leaks": sum(hit["document_id"] not in documents for hit in observed),
            "citation_errors": citation_errors(observed, units, nodes),
            "final_tokens": final_tokens, "warm_latency_ms": elapsed,
            "result": result,
        }


async def main(args):
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    rows = []
    try:
        # Exclude service/model cold start from subsequent measurements.
        await measure(manifest, CASES[0], args.budgets[0])
        for budget in args.budgets:
            for case in CASES:
                row = await measure(manifest, case, budget)
                rows.append(row)
                print(f"{case['id']} budget={budget}: {row['covered_necessary']}/{row['necessary_total']} spans, {row['status']}", flush=True)
            report = {
                "generated_at": datetime.now(UTC).isoformat(), "release_approved": False,
                "complete": len(rows) == len(CASES) * len(args.budgets),
                "annotation_review": "agent-authored demo spans; independent review pending",
                "scope": "read-only deployed retrieval layer, authored XL-107 demo; not a formal locked quality/model comparison",
                "identity": model_identity(), "case_count": len(CASES),
                "dataset_sha256": hashlib.sha256(json.dumps(CASES, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                "metrics": summarize(rows),
                "per_budget": {str(b): summarize([r for r in rows if r['budget'] == b]) for b in args.budgets if any(r['budget'] == b for r in rows)},
                "rows": rows,
            }
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report["metrics"], ensure_ascii=True), flush=True)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budgets", type=int, nargs="+", choices=(600, 1200, 1800), default=[1200])
    args = parser.parse_args()
    asyncio.run(main(args))
