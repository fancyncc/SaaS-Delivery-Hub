"""Record source-pinned development questions in one isolated temporary tenant."""

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import get_settings
from backend.db import engine
from backend.rag_v3_policy import PIPELINE_SCHEMA
from backend.rag_v3_release import model_identity
from scripts.evaluate_v3_demo import summarize
from scripts.record_v3_boundary_runs import record_format


def implementation_checksums():
    import backend.rag_v3

    root = Path(backend.rag_v3.__file__).resolve().parent
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            if (root / name).is_file() else None for name in (
                "rag_v3.py", "rag_v3_index.py", "rag_v3_binary.py", "rag_v3_context.py",
                "rag_v3_evidence.py", "rag_v3_parse_runner.py", "rag_v3_policy.py", "rag_v3_release.py",
            )}


async def main(args):
    if get_settings().rag_mode != "real" or get_settings().rag_v3_relevance_mode != "rules":
        raise ValueError("Requires real retrieval services in rules mode")
    registry = json.loads(args.cases.with_name("sources.json").read_text(encoding="utf-8"))
    for source in registry["sources"]:
        path = (args.cases.parent / source["document"]).resolve()
        if not path.is_relative_to(args.cases.parent.resolve()):
            raise ValueError("Source path must remain within corpus")
        if hashlib.sha256(path.read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError("Source checksum mismatch: " + source["document"])
    cases = [json.loads(line) for line in args.cases.read_text(encoding="utf-8").splitlines()]
    registered = {s["document"] for s in registry["sources"]}
    if not cases or any(c["document"] not in registered for c in cases):
        raise ValueError("Cases must name registered sources")
    args.source_license = "pinned repository document or public US government work"
    try:
        rows, indexed = await record_format("mixed", cases, args)
        report = {
            "generated_at": datetime.now(UTC).isoformat(), "complete": True,
            "release_approved": False, "independent_review_completed": False,
            "scope": registry["scope"], "customer_documents": False,
            "cases_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
            "sources": registry["sources"], "identity": model_identity(),
            "pipeline_schema": PIPELINE_SCHEMA, "relevance_mode": "rules",
            "implementation_sha256": implementation_checksums(),
            "test_data_cleaned": True, "metrics": summarize(rows),
            "per_language": {lang: summarize([r for r in rows if r["language"] == lang]) for lang in ("zh", "en")},
            "per_format": {fmt: summarize([r for r in rows if r["format"] == fmt]) for fmt in ("md", "pdf")},
            "index_metrics": indexed, "rows": rows,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report["metrics"], ensure_ascii=True), flush=True)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path("evaluations/v3/business/cases.jsonl"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budget", type=int, choices=(600, 1200, 1800), default=1200)
    asyncio.run(main(parser.parse_args()))
