import copy

import pytest

from scripts import verify_runtime_services as services
from scripts.verify_restored_database import TABLES, compare


async def test_service_report_cannot_turn_missing_or_failed_checks_into_acceptance(monkeypatch):
    async def passed():
        return services.result("passed")
    async def missing():
        return services.result("not_configured")
    async def secret_error():
        raise ConnectionError("password=private-secret")
    for name in ("database", "redis", "smtp_connection", "search", "generation", "retrieval", "storage", "worker"):
        monkeypatch.setattr(services, name, passed)
    monkeypatch.setattr(services, "generation", missing)
    monkeypatch.setattr(services, "storage", secret_error)
    report = await services.run(exercise=True)
    assert not report["technical_checks_passed"] and not report["production_acceptance"]
    assert "private-secret" not in str(report)
    assert report["checks"]["object_storage"]["error_type"] == "ConnectionError"


def test_restore_detects_content_difference_even_with_equal_counts():
    expected = {"schema_revision": "0030_execution_safety",
                "tables": {name: {"count": 3, "sha256": "a"} for name in (*TABLES, "role_permissions", "support_access_grant_permissions")}}
    actual = copy.deepcopy(expected)
    assert compare(expected, actual)["database_business_verified"]
    actual["tables"]["approval_requests"]["sha256"] = "different-approval"
    report = compare(expected, actual)
    assert not report["database_business_verified"]
    assert report["mismatches"] == ["approval_requests"]
    assert not report["object_storage_verified"] and not report["production_acceptance"]


@pytest.mark.parametrize("table", ["agent_actions", "agent_memories", "agent_lessons", "idempotency_records"])
def test_restore_requires_agent_journal_and_request_replay_state(table):
    expected = {"schema_revision": "0030_execution_safety",
                "tables": {name: {"count": 1, "sha256": "a" * 64} for name in (*TABLES, "role_permissions", "support_access_grant_permissions")}}
    actual = copy.deepcopy(expected)
    actual["tables"][table]["sha256"] = "b" * 64
    assert compare(expected, actual)["mismatches"] == [table]
    # Legacy manifests with the table omitted on both sides must also fail.
    del expected["tables"][table]
    del actual["tables"][table]
    assert not compare(expected, actual)["database_business_verified"]


async def test_model_authentication_failure_is_reported_without_response_secrets():
    from fastapi import HTTPException

    async def rejected():
        raise HTTPException(503, "模型服务认证失败", headers={"Authorization": "private-secret"})
    result = await services.capture(rejected)
    assert result == {"status": "failed", "error_type": "HTTPException", "reason": "MODEL_AUTHENTICATION_FAILED"}
