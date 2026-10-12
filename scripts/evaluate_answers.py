"""Generate paired answers through the application adapter, or score reviewed receipts."""

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.evaluation_artifacts import (
    RunJournal,
    canonical,
    digest,
    read_rows,
    runtime_config,
    verify_frozen,
    write_once,
)
from backend.evaluation_scoring import answer_report
from backend.evaluation_validation import check_envelope


async def collect(cases, recording, output):
    from backend.chat import answer_question
    from backend.config import get_settings
    from backend.model_observations import capture_model_receipts, measured_usage

    check_envelope(recording)
    if get_settings().model_mode != "real":
        raise ValueError("Answer quality requires MODEL_MODE=real")
    frozen = recording["frozen"]
    paired_frozen = {**frozen, "corpus": {**frozen["corpus"], "case_ids":
                     [c["id"] + ":" + m for c in cases for m in ("retrieved", "oracle")]}}
    journal = RunJournal(output, paired_frozen)
    raw_rows = {r["id"]: r for r in recording["rows"]}
    units = list(recording["source_snapshot"]["units"].values())
    documents = recording["source_snapshot"]["documents"]
    rows = []
    try:
        for case in cases:
            for mode in ("retrieved", "oracle"):
                hits = (raw_rows[case["id"]]["result"]["chunks"] if mode == "retrieved" else
                        [u for u in units if documents[u["document_id"]]["document"] == case["document"]])
                # Oracle uses source text, never labels or expected answers as model input.
                sources = [dict(h, kind="customer_document") for h in hits]
                started = time.perf_counter()
                row = {"id": case["id"], "mode": mode, "evidence": hits}
                try:
                    with capture_model_receipts() as receipts:
                        answer = await answer_question(case["question"], [], "", sources)
                    receipt = receipts[-1] if receipts else {}
                    row.update(outcome="succeeded", answer=answer["answer"],
                        citations=[{"source_id": c["id"]} for c in answer["citations"]],
                        abstained=bool(re.search(r"未(?:找到|检索到)|无法(?:确认|回答)|证据不足|资料不足|cannot (?:answer|determine)|insufficient evidence|not (?:provided|specified)", answer["answer"], re.I)),
                        provider_model=receipt.get("provider_model"),
                        provider_response_id=receipt.get("provider_response_id"),
                        usage=measured_usage(receipts), model_receipts=receipts)
                except Exception as exc:
                    row.update(outcome="timeout" if isinstance(exc, TimeoutError) else "failed",
                               error_type=type(exc).__name__, model_receipts=receipts,
                               usage=measured_usage(receipts))
                row["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
                rows.append(row)
                journal.append({**row, "case_id": row["id"], "id": row["id"] + ":" + mode})
    except BaseException as exc:
        journal.finish("interrupted", cleanup=True, errors=[type(exc).__name__],
                       answer_rows=rows, retrieval_run_id=recording["run_id"])
        raise
    return journal, rows


async def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("collect", "score"))
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Output exists; choose a new run path")
    try:
        recording = json.loads(args.runs.read_bytes())
        frozen = recording.get("original_frozen", recording["frozen"])
        verify_frozen(frozen, args.cases, config=runtime_config(frozen["config"]["budget"]) if args.action == "collect" else None)
        cases = [c for c in read_rows(args.cases) if c["split"] == "validation"]
        if not cases:
            raise ValueError("No locked validation questions")
        if args.action == "collect":
            journal, rows = await collect(cases, recording, args.output)
            errors = []
            try:
                verify_frozen(frozen, args.cases, config=runtime_config(frozen["config"]["budget"]))
            except ValueError:
                errors.append("inputs_changed_during_generation")
            succeeded = not errors and all(r["outcome"] == "succeeded" for r in rows)
            journal.finish("succeeded" if succeeded else "failed", cleanup=True, errors=errors,
                answer_rows=rows, retrieval_run_id=recording["run_id"],
                original_frozen=frozen, answer_rows_sha256=digest(canonical(rows)))
            return 0 if succeeded else 1
        if (recording.get("frozen_sha256") != digest(canonical(recording["frozen"]))
                or not recording.get("complete") or recording.get("status") != "succeeded"
                or recording.get("errors")
                or recording.get("rows_sha256") != digest(canonical(recording["rows"]))
                or recording.get("answer_rows_sha256") != digest(canonical(recording["answer_rows"]))):
            raise ValueError("Incomplete or tampered answer recording")
        review = json.loads(args.review.read_bytes()) if args.review else None
        result = answer_report(cases, recording["answer_rows"], review=review)
        result.update(schema=1, run_id=recording["run_id"], implementation_sha256=frozen["implementation_sha256"],
                      frozen_sha256=digest(canonical(frozen)), scope="locked_answer_validation")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result = {"passed": False, "failures": [type(exc).__name__], "status": "invalid"}
    write_once(args.output, result)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(asyncio.run(main()))
