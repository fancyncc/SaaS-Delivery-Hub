"""Run behavioral suites sequentially and collect machine-readable release evidence."""

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.evaluation_acceptance import aggregate, junit_result, load_reports, performance_result
from backend.evaluation_artifacts import (
    POLICY,
    ROOT,
    digest,
    environment,
    implementation_checksums,
    write_once,
)

SUITES = {
    "agent": ["tests/test_agent_loop.py", "tests/test_tool_skill_integration.py", "tests/test_execution_snapshots.py"],
    "memory": ["tests/test_context_reliability.py", "tests/test_conversation_context.py", "tests/test_scoped_memory.py"],
    "distributed_trace": ["tests/test_execution_tracing.py"],
    "browser": ["tests/test_browser_flow.py", "tests/test_browser_onboarding.py", "tests/test_agent_browser.py", "tests/test_chat_browser.py"],
    "backend": ["tests", "--ignore=tests/test_browser_flow.py", "--ignore=tests/test_browser_onboarding.py",
                "--ignore=tests/test_agent_browser.py", "--ignore=tests/test_chat_browser.py"],
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("suite", "aggregate", "performance", "isolated"))
    parser.add_argument("--suite", choices=(*SUITES, "all"), default="all")
    parser.add_argument("--report", action="append", default=[], help="check_name=report.json")
    parser.add_argument("--samples", type=Path)
    parser.add_argument("--freeze", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.action == "isolated":
        args.output.mkdir(parents=True, exist_ok=False)
        before = implementation_checksums()
        reply = subprocess.run([sys.executable, str(ROOT / "scripts/run_isolated_execution_acceptance.py"),
                                "--output-dir", str(args.output / "raw")], cwd=ROOT)
        raw_path = args.output / "raw/isolated_execution.json"
        raw = json.loads(raw_path.read_bytes()) if raw_path.exists() else {}
        pg_path = args.output / "raw/postgres.json"
        pg = json.loads(pg_path.read_bytes()) if pg_path.exists() else {}
        valid = not reply.returncode and before == implementation_checksums()
        checks = {
            "postgres_rls": (valid and all(pg.get(k) == "passed" for k in
                ("postgres_rls", "pool_context_switch", "concurrent_run_start", "snapshot_immutability", "committed_worker_replay")), "real_postgres"),
            "worker_recovery": (valid and raw.get("worker_sigkill_recovery") == "passed", "real_worker_recovery"),
            "backup_restore": (valid and raw.get("database_restore", {}).get("database_business_verified") is True
                               and raw.get("database_restore", {}).get("object_storage_verified") is True, "real_backup_restore"),
            "distributed_trace": (valid and raw.get("api_worker_trace_storage") == "passed", "real_cross_process_trace"),
        }
        identifier = uuid4().hex
        for name, (passed, scope) in checks.items():
            result = {"schema": 1, "run_id": identifier, "scope": scope,
                      "status": "passed" if passed else "failed", "implementation_sha256": before,
                      "evidence_sha256": digest(raw_path.read_bytes()) if raw_path.exists() else None}
            if args.freeze:
                result["frozen_sha256"] = digest(args.freeze.read_bytes().rstrip())
            write_once(args.output / (name + ".json"), result)
        return 0 if all(c[0] for c in checks.values()) else 1
    if args.action == "suite":
        args.output.mkdir(parents=True, exist_ok=False)
        run_id = uuid4().hex
        before = implementation_checksums()
        write_once(args.output / "run.json", {"schema": 1, "run_id": run_id, "status": "started",
                   "implementation_sha256": before, "suites": list(SUITES) if args.suite == "all" else [args.suite],
                   "started_at": datetime.now(UTC).isoformat()})
        failed = False
        for name in SUITES if args.suite == "all" else (args.suite,):
            junit = args.output / (name + ".xml")
            log = args.output / (name + ".log")
            with log.open("xb") as stream:
                try:
                    reply = subprocess.run([sys.executable, "-m", "pytest", *SUITES[name], "-q",
                                        "--junitxml=" + str(junit)], cwd=ROOT, env=os.environ.copy(),
                                       stdout=stream, stderr=subprocess.STDOUT)
                except KeyboardInterrupt:
                    result = {"schema": 1, "run_id": run_id, "status": "interrupted", "suite": name,
                              "scope": "application_regression", "implementation_sha256": before,
                              "counts": junit_result(junit)["counts"] if junit.exists() else None}
                    write_once(args.output / (name + ".json"), result)
                    return 130
            report = junit_result(junit) if junit.exists() else {"status": "failed", "reason": "junit_missing"}
            report.update(schema=1, run_id=run_id, suite=name, scope="application_regression",
                          generated_at=datetime.now(UTC).isoformat(), exit_code=reply.returncode,
                          implementation_sha256=before, environment=environment())
            if reply.returncode or implementation_checksums() != before:
                report["status"] = "failed"
            if args.freeze:
                report["frozen_sha256"] = digest(args.freeze.read_bytes().rstrip())
            write_once(args.output / (name + ".json"), report)
            failed |= report["status"] != "passed"
            print(json.dumps({"suite": name, "status": report["status"], "counts": report.get("counts")}))
        return int(failed)
    try:
        frozen = json.loads(args.freeze.read_bytes()) if args.freeze else None
        if args.action == "performance":
            if not frozen:
                raise ValueError("Performance needs frozen target environment and limits")
            samples = json.loads(args.samples.read_bytes())
            if (samples.get("implementation_sha256") != implementation_checksums()
                    or samples.get("frozen_sha256") != digest(args.freeze.read_bytes().rstrip())
                    or samples.get("environment") != frozen["environment"]):
                raise ValueError("Unbound performance samples")
            result = performance_result(samples["rows"], frozen["policy"].get("performance"))
            result.update(schema=1, run_id=samples["run_id"], implementation_sha256=implementation_checksums(),
                          frozen_sha256=digest(args.freeze.read_bytes().rstrip()), scope="target_environment_performance")
        else:
            policy = frozen["policy"] if frozen else json.loads(POLICY.read_bytes())
            result = aggregate(policy["required_acceptance"], load_reports(args.report),
                               expected_frozen=digest(args.freeze.read_bytes().rstrip()) if frozen else None)
            result["run_id"] = uuid4().hex
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result = {"passed": False, "status": "invalid", "error_type": type(exc).__name__}
    write_once(args.output, result)
    return 0 if result.get("passed") or result.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
