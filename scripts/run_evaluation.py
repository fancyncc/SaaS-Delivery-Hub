"""Freeze, collect and score real retrieval/answer runs; never silently approve missing review."""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.evaluation_artifacts import ROOT, write_once


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--answer-review", type=Path)
    parser.add_argument("--budget", type=int, choices=(600, 1200, 1800), default=1200)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    paths = {name: args.output_dir / (name + ".json") for name in
             ("frozen", "retrieval", "retrieval-score", "answers", "answer-score")}
    cases = ["--cases", str(args.cases)]
    stages = [
        ("freeze", "evaluate_retrieval.py", ["freeze", *cases, "--budget", str(args.budget), "--output", str(paths["frozen"])], []),
        ("collect_retrieval", "record_retrieval_business_runs.py", [*cases, "--budget", str(args.budget), "--freeze", str(paths["frozen"]), "--output", str(paths["retrieval"])], ["frozen"]),
        ("score_retrieval", "evaluate_retrieval.py", ["validate", *cases, "--runs", str(paths["retrieval"]), "--output", str(paths["retrieval-score"])], ["retrieval"]),
        ("collect_answers", "evaluate_answers.py", ["collect", *cases, "--runs", str(paths["retrieval"]), "--output", str(paths["answers"])], ["retrieval"]),
        ("score_answers", "evaluate_answers.py", ["score", *cases, "--runs", str(paths["answers"]), "--output", str(paths["answer-score"])], ["answers"]),
    ]
    optional = {"freeze": ("--review", args.review), "score_retrieval": ("--baseline", args.baseline),
                "score_answers": ("--review", args.answer_review)}
    checks = {}
    prerequisites = {"frozen": "freeze", "retrieval": "collect_retrieval", "answers": "collect_answers"}
    for name, script, command, dependencies in stages:
        if not all(paths[dep].exists() and checks[prerequisites[dep]]["status"] == "passed" for dep in dependencies):
            checks[name] = {"status": "not_run", "reason": "prerequisite_failed"}
            continue
        flag, value = optional.get(name, (None, None))
        if value:
            command += [flag, str(value)]
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / script), *command], cwd=ROOT)
        checks[name] = {"status": "passed" if result.returncode == 0 else "failed", "exit_code": result.returncode}
    passed = all(check["status"] == "passed" for check in checks.values())
    write_once(args.output_dir / "summary.json", {"passed": passed, "checks": checks,
               "production_acceptance": False, "remaining": ["engineering_acceptance", "model_comparison"]})
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
