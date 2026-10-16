from sqlalchemy import select

from backend.db import SessionLocal
from backend.models import MailDelivery


async def mail_link(purpose: str) -> str:
    async with SessionLocal() as session:
        row = await session.scalar(select(MailDelivery).where(MailDelivery.purpose == purpose)
            .order_by(MailDelivery.created_at.desc()).limit(1))
        assert row is not None
        return row.body
