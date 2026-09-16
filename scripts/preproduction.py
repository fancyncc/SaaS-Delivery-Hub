"""Fixed-project lifecycle commands. Never targets the local SQLite application."""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ["docker", "compose", "--project-name", "saas-preproduction", "--env-file",
           str(ROOT / ".env.preproduction"), "-f", str(ROOT / "docker-compose.preproduction.yml")]


def run(args, timeout=60):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=timeout)


def docker_report():
    try:
        result = run(["docker", "info", "--format", "{{json .}}"], 15)
        info = json.loads(result.stdout) if result.returncode == 0 else {}
        available = bool(info.get("ServerVersion")) and info.get("OSType") == "linux"
    except (OSError, ValueError, subprocess.TimeoutExpired):
        info, available = {}, False
    return {"docker_available": available, "docker_cpus": info.get("NCPU"),
            "docker_memory_bytes": info.get("MemTotal"), "workspace_free_bytes": shutil.disk_usage(ROOT).free}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "preflight", "start", "status", "stop"))
    args = parser.parse_args()
    if args.action == "prepare":
        from prepare_cpu_preproduction import prepare
        print(json.dumps(prepare(), ensure_ascii=False))
        return 0
    state = docker_report()
    state["configuration_present"] = all((ROOT / name).is_file() for name in (
        ".env.preproduction", ".env.preproduction.runtime", ".env.preproduction.models"))
    if state["configuration_present"]:
        try:
            state["compose_valid"] = run([*COMPOSE, "config", "--quiet"]).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            state["compose_valid"] = False
    else:
        state["compose_valid"] = False
    state["real_models_verified"] = False
    if args.action == "preflight":
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 0 if state["docker_available"] and state["compose_valid"] else 1
    if not state["docker_available"] or not state["compose_valid"]:
        print(json.dumps({**state, "action": "Restore Docker and run prepare/preflight before continuing."}))
        return 1
    if args.action == "stop":
        command = [*COMPOSE, "stop"]  # Keep containers and all named volumes.
    elif args.action == "status":
        result = run([*COMPOSE, "ps", "--format", "json"])
        try:
            services = json.loads(result.stdout) if result.stdout.strip().startswith("[") else [
                json.loads(line) for line in result.stdout.splitlines() if line.strip()]
            state["services"] = [{key: service.get(key) for key in ("Service", "State", "Health")} for service in services]
            state["real_models_verified"] = any(service.get("Service") == "retrieval" and service.get("Health") == "healthy" for service in services)
        except ValueError:
            state["services"] = []
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return result.returncode
    else:
        for step in ([*COMPOSE, "build", "api", "retrieval"],
                     [*COMPOSE, "run", "--rm", "model-download"]):
            # Long downloads/builds stay visible in the invoking terminal.
            if subprocess.call(step, cwd=ROOT) != 0:
                return 1
        command = [*COMPOSE, "up", "-d", "--wait", "--wait-timeout", "300"]
    return subprocess.call(command, cwd=ROOT)


if __name__ == "__main__":
    sys.exit(main())
