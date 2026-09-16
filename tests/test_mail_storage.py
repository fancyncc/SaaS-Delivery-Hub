import pytest
from fastapi import HTTPException
from sqlalchemy import select

from backend import mailer, storage
from backend.config import get_settings
from backend.db import SessionLocal
from backend.models import MailDelivery, ProjectArtifact


async def test_mail_delivery_tracks_failure_and_removes_token(monkeypatch):
    monkeypatch.setattr(get_settings(), "mail_debug", False)
    await mailer.send_account_link("test@example.test", "invitation", "https://example.test/?token=private-test")
    def failed(_):
        raise ConnectionError("smtp unavailable")
    monkeypatch.setattr(mailer, "smtp_send", failed)
    for _ in range(3):
        await mailer.flush_mail()
    async with SessionLocal() as session:
        item = await session.scalar(select(MailDelivery))
        assert item.status == "unknown" and item.attempts == 1
        assert item.body == "" and item.last_error == "ConnectionError"


async def test_mail_crash_after_acceptance_is_not_replayed(monkeypatch):
    monkeypatch.setattr(get_settings(), "mail_debug", False)
    await mailer.send_account_link("test@example.test", "invitation", "https://example.test/link")
    accepted = []

    class WorkerLost(BaseException):
        pass

    def accepted_then_crashed(item):
        accepted.append(item.id)
        raise WorkerLost()

    monkeypatch.setattr(mailer, "smtp_send", accepted_then_crashed)
    with pytest.raises(WorkerLost):
        await mailer.flush_mail()
    await mailer.flush_mail()
    async with SessionLocal() as session:
        item = await session.scalar(select(MailDelivery))
        assert item.status == "sending" and item.attempts == 1
    assert len(accepted) == 1


async def test_corrupt_object_is_not_delivered(monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_backend", "s3")
    class Body:
        def read(self):
            return b"corrupted"
    class Client:
        def get_object(self, **_):
            return {"Body": Body()}
    monkeypatch.setattr(storage, "client", Client)
    item = ProjectArtifact(tenant_id="t", project_id="p", checksum="original", content="original")
    with pytest.raises(HTTPException) as error:
        await storage.read(item)
    assert error.value.status_code == 503
