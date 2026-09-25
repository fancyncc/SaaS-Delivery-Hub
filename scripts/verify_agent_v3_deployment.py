"""Verify the deployed Agent knowledge entry uses V3's final project evidence."""

import json
import subprocess
from pathlib import Path
from uuid import UUID

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    workspace = ROOT / "data" / "demo_workspace"
    admin = json.loads((workspace / "credentials.json").read_text(encoding="utf-8"))["admin"]
    manifest = json.loads((workspace / "manifest.json").read_text(encoding="utf-8"))
    tenant = str(UUID(manifest["tenant_id"]))
    question = "XL-107 P1 首次响应时限是多少？"
    with httpx.Client(base_url="http://127.0.0.1:8080", trust_env=False, timeout=180) as client:
        def post(path, payload):
            response = client.post(path, json=payload,
                headers={"X-CSRF-Token": client.cookies.get("saas_csrf", "")})
            response.raise_for_status()
            return response.json()["data"]

        post("/api/auth/login", {"username": admin["username"], "password": admin["password"]})
        post(f"/api/auth/spaces/{tenant}/switch", {})
        projects = client.get("/api/projects")
        projects.raise_for_status()
        project = str(UUID(next(item["id"] for item in projects.json()["data"]
                                if "XL-107" in item["name"])))
        selected = post("/api/knowledge/inspect", {"question": question, "project_id": project})["chunks"]
    assert selected
    code = f"""import asyncio, json
from backend.db import SessionLocal
from backend.knowledge import retrieve
async def main():
    async with SessionLocal() as session:
        session.info['rls_context'] = {{'app.current_tenant_id': {tenant!r}}}
        hits = await retrieve(session, {tenant!r}, {question!r}, project_ids=[{project!r}])
        print(json.dumps([{{'id': hit['id'], 'title': hit['title']}} for hit in hits]))
asyncio.run(main())"""
    completed = subprocess.run(
        ["docker", "compose", "exec", "-T", "api", "python", "-c", code],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    hits = json.loads(completed.stdout)
    assert hits and {hit["id"] for hit in hits} <= {item["id"] for item in selected}
    assert any("XL-107 SLA" in hit["title"] for hit in hits)
    print(json.dumps({"agent_knowledge": "passed", "selected_v3_units": len(selected),
                      "agent_citations": len(hits)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
