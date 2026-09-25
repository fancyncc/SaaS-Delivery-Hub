"""Check deployed chat citations against the V3 inspection's final evidence."""

import json
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx


def main():
    root = Path(__file__).resolve().parents[1] / "data/demo_workspace"
    admin = json.loads((root / "credentials.json").read_text(encoding="utf-8"))["admin"]
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    question = "XL-107 P1 首次响应时限是多少？"
    with httpx.Client(base_url="http://127.0.0.1:8080", trust_env=False, timeout=180) as client:
        def post(path, payload):
            response = client.post(path, json=payload,
                headers={"X-CSRF-Token": client.cookies.get("saas_csrf", "")})
            if response.status_code >= 400:
                print(json.dumps({"path": path, "status": response.status_code,
                    "detail": response.json().get("detail")}, ensure_ascii=False))
            response.raise_for_status()
            return response.json()["data"]

        post("/api/auth/login", {"username": admin["username"], "password": admin["password"]})
        post(f"/api/auth/spaces/{manifest['tenant_id']}/switch", {})
        features = client.get("/api/chat/conversations").json()["data"]["features"]
        if "--history" in sys.argv:
            assert features["memory_items"] is True
        if "--candidates" in sys.argv:
            assert features["memory_candidates"] is True
            preference = client.get("/api/chat/memory-preferences")
            preference.raise_for_status()
            assert preference.json()["data"]["auto_extract"] is False
        selected = post("/api/knowledge/inspect", {"question": question})["chunks"]
        assert selected
        selected_ids = {item["id"] for item in selected}
        conversation = post("/api/chat/conversations", {})
        succeeded = False
        try:
            result = post(f"/api/chat/conversations/{conversation['id']}/messages", {
                "question": question, "expected_version": conversation["version"],
                "request_id": str(uuid4())})
            message = result["messages"][-1]
            knowledge_citations = [item for item in message["citations"]
                if item["title"].startswith("XL-107 SLA")]
            assert knowledge_citations
            assert {item["id"] for item in knowledge_citations} <= selected_ids
            if "--history" in sys.argv:
                page = client.get(f"/api/chat/conversations/{conversation['id']}/messages")
                page.raise_for_status()
                history = page.json()["data"]
                assert history["message_count"] == 1
                assert history["messages"][0]["question"] == question
            if "--context" in sys.argv:
                for _ in range(15):
                    response = client.get(f"/api/chat/conversations/{conversation['id']}/context")
                    response.raise_for_status()
                    context = response.json()["data"]
                    if context["processed_turn"] >= 1:
                        break
                    time.sleep(2)
                assert context["processed_turn"] >= 1, context["status"]
            print(json.dumps({"chat": "passed", "mode": message["mode"],
                "selected_v3_units": len(selected_ids), "cited_v3_units": len(knowledge_citations),
                "context_processed": context["processed_turn"] if "--context" in sys.argv else None},
                ensure_ascii=False))
            succeeded = True
        finally:
            if succeeded or "--keep-on-error" not in sys.argv:
                response = client.delete(f"/api/chat/conversations/{conversation['id']}",
                    headers={"X-CSRF-Token": client.cookies.get("saas_csrf", "")})
                response.raise_for_status()
            else:
                print(json.dumps({"debug_conversation_id": conversation["id"]}))


if __name__ == "__main__":
    main()
