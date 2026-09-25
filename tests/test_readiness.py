import pytest
from fastapi import HTTPException

from backend import db, main


class FakeSession:
    def __init__(self, revision=None, error=None):
        self.revision = revision
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def scalar(self, _):
        if self.error:
            raise self.error
        return self.revision


async def test_readiness_rejects_schema_lag(monkeypatch):
    monkeypatch.setattr(db, "SessionLocal", lambda: FakeSession("0021_retrieval_sources"))
    with pytest.raises(HTTPException) as exc:
        await main.ready()
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "SCHEMA_NOT_READY"


async def test_readiness_rejects_database_failure(monkeypatch):
    monkeypatch.setattr(db, "SessionLocal", lambda: FakeSession(error=ConnectionError()))
    with pytest.raises(HTTPException) as exc:
        await main.ready()
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "DATABASE_NOT_READY"
