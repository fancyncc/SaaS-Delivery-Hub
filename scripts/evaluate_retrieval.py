"""Freeze inputs, calibrate development rows, or validate bound locked recordings."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.evaluation_artifacts import (
    POLICY,
    freeze,
    read_rows,
    runtime_config,
    verify_frozen,
    write_once,
)
from backend.evaluation_validation import calibration_rows, validate_recording
from backend.retrieval_evaluation import calibrate, compare_models


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "calibrate", "validate", "compare"))
    parser.add_argument("--cases", type=Path, default=Path("evaluations/retrieval/cases.jsonl"))
    parser.add_argument("--runs", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--comparison", type=Path)
    parser.add_argument("--policy", type=Path, default=POLICY)
    parser.add_argument("--budget", type=int, choices=(600, 1200, 1800), default=1200)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Output exists; choose a new run path")
    try:
        if args.action == "freeze":
            output = freeze(args.cases, runtime_config(args.budget), args.policy, review_path=args.review)
        elif args.action == "calibrate":
            cases = read_rows(args.cases)
            rows = read_rows(args.runs)
            try:
                recording = json.loads(args.runs.read_bytes())
            except json.JSONDecodeError:
                recording = None
            if isinstance(recording, dict) and "frozen" in recording:
                verify_frozen(recording["frozen"], args.cases, policy_path=args.policy)
                rows = calibration_rows(cases, recording)
            ids = {c["id"] for c in cases if c["split"] == "calibration"}
            if not rows or any(r.get("id") not in ids for r in rows):
                raise ValueError("Calibration rows must reference calibration cases")
            output = calibrate(rows)
        elif args.action == "compare":
            output = compare_models(json.loads(args.runs.read_bytes()), json.loads(args.baseline.read_bytes()))
        else:
            cases = read_rows(args.cases)
            report = json.loads(args.runs.read_bytes())
            baseline = json.loads(args.baseline.read_bytes()) if args.baseline else None
            output = validate_recording(cases, report, args.cases, baseline=baseline, policy_path=args.policy)
            if args.comparison:
                comparison = json.loads(args.comparison.read_bytes())
                if (comparison.get("passed") is not True or comparison.get("run_id") != output["run_id"]
                        or comparison.get("frozen_sha256") != output["frozen_sha256"]
                        or comparison.get("identity") != output["identity"]
                        or comparison.get("sources_sha256") != output["sources_sha256"]):
                    raise ValueError("Model comparison does not authorize this run")
                output["model_comparison_passed"] = True
    except (OSError, ValueError, KeyError, TypeError) as exc:
        output = {"passed": False, "status": "invalid", "failures": [str(exc)], "release_approved": False}
    write_once(args.output, output)
    print(json.dumps({"output": str(args.output), "passed": output.get("passed"),
                      "failures": output.get("failures", [])}, ensure_ascii=False))
    return 1 if output.get("passed") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
