"""Exercise a new V3 upload in local Docker, then remove only this probe document."""

import json
import subprocess
import time
from pathlib import Path
from uuid import UUID, uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]


def cleanup(document_id, title):
    config = json.loads(subprocess.check_output(
        ["docker", "compose", "config", "--format", "json"], cwd=ROOT, text=True,
    ))
    database = config["services"]["postgres"]["environment"]["POSTGRES_DB"]
    owner = config["services"]["postgres"]["environment"]["POSTGRES_USER"]
    if not all(value.replace("_", "").isalnum() for value in (database, owner)):
        raise ValueError("Unexpected database identity")
    identifier = str(UUID(document_id))
    assert title.startswith(("索引验收临时资料-", "空白页验收临时资料-"))
    sql = f"""BEGIN;
DELETE FROM retrieval_sources WHERE kind='knowledge' AND origin_id='{identifier}';
DELETE FROM rag_v3_documents WHERE origin_id='{identifier}';
DELETE FROM knowledge_chunks WHERE document_id='{identifier}';
DELETE FROM knowledge_documents WHERE id='{identifier}' AND title='{title}';
COMMIT;"""
    completed = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "psql", "-v", "ON_ERROR_STOP=1",
         "-U", owner, "-d", database, "-c", sql],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    assert completed.stdout.splitlines()[-2] == "DELETE 1", completed.stdout


def main():
    workspace = ROOT / "data" / "demo_workspace"
    admin = json.loads((workspace / "credentials.json").read_text(encoding="utf-8"))["admin"]
    manifest = json.loads((workspace / "manifest.json").read_text(encoding="utf-8"))
    title = f"索引验收临时资料-{uuid4().hex[:12]}"
    document_id = None
    with httpx.Client(base_url="http://127.0.0.1:8080", trust_env=False, timeout=30) as client:
        def post(path, payload):
            response = client.post(path, json=payload,
                headers={"X-CSRF-Token": client.cookies.get("saas_csrf", "")})
            response.raise_for_status()
            return response.json()["data"]

        post("/api/auth/login", {"username": admin["username"], "password": admin["password"]})
        post(f"/api/auth/spaces/{manifest['tenant_id']}/switch", {})
        try:
            uploaded = post("/api/knowledge", {
                "title": title, "version": 1, "module": "本机验收", "source": "临时原文",
                "license": "本机索引验收", "body": "# 索引验收\n\n本资料仅用于本机 Docker 索引生命周期验收。索引成功后将自动清理。",
            })
            document_id = uploaded["id"]
            assert uploaded["index_status"] in {"pending", "v3_pending"}, uploaded
            phases = []
            for _ in range(60):
                response = client.get(f"/api/knowledge/{document_id}")
                response.raise_for_status()
                detail = response.json()["data"]
                phase = detail["v3"]["phase"]
                phases.append(phase)
                if phase in {"ready", "failed"}:
                    break
                time.sleep(3)
            assert phases[-1] == "ready", detail["v3"]
            print(json.dumps({"upload": "passed", "initial_status": uploaded["index_status"],
                              "final_phase": phases[-1], "observed_phases": sorted(set(phases))},
                             ensure_ascii=False))
        finally:
            if document_id:
                cleanup(document_id, title)


if __name__ == "__main__":
    main()
