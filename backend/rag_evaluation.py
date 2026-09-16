"""Evaluate explicitly labelled cases against the real chunk retrieval service."""
import json
import statistics
import time
from pathlib import Path

from backend.knowledge import retrieve
from backend.rag import search

DATASET = Path(__file__).resolve().parents[1] / "evaluations/rag_v2.jsonl"


async def evaluate_retrieval(session, tenant_id: str, path: Path = DATASET) -> dict:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("duplicate evaluation IDs")
    details = []
    for case in cases:
        start = time.perf_counter()
        hits = await retrieve(session, tenant_id, case["query"])
        elapsed = (time.perf_counter() - start) * 1000
        baseline = search(case["query"])
        expected = set(case["expected_titles"])
        titles = {h["title"] for h in hits}
        baseline_titles = {h["title"] for h in baseline}
        details.append({"id": case["id"], "answerable": bool(expected),
            "recall": len(expected & titles) / len(expected) if expected else None,
            "baseline_recall": len(expected & baseline_titles) / len(expected) if expected else None,
            "citation_precision": sum(h["title"] in expected for h in hits) / len(hits) if hits else None,
            "correct_abstention": not hits if not expected else None,
            "latency_ms": round(elapsed, 2), "retrieved_titles": sorted(titles)})
    positive = [d for d in details if d["answerable"]]
    negative = [d for d in details if not d["answerable"]]
    return {"dataset_size": len(cases), "human_reviewed": sum(c.get("review_status") == "human_verified" for c in cases),
        "recall_at_5": statistics.mean(d["recall"] for d in positive),
        "baseline_recall_at_5": statistics.mean(d["baseline_recall"] for d in positive),
        "no_answer_accuracy": statistics.mean(d["correct_abstention"] for d in negative) if negative else None,
        "mean_latency_ms": statistics.mean(d["latency_ms"] for d in details),
        "p95_latency_ms": sorted(d["latency_ms"] for d in details)[max(0, (95 * len(details) + 99) // 100 - 1)],
        "provider_cost": None, "cost_note": "Provider billing is unavailable; do not interpret as zero cost.",
        "cases": details}
