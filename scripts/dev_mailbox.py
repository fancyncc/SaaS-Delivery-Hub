"""Private local outbox reader. Never expose this through an HTTP endpoint."""
import argparse
import asyncio

from sqlalchemy import select

from backend.config import get_settings
from backend.db import SessionLocal
from backend.models import MailDelivery


async def main(email: str):
    settings = get_settings()
    if settings.app_env == "production" or not settings.mail_debug:
        raise SystemExit("Only available with development MAIL_DEBUG=true")
    async with SessionLocal() as session:
        items = await session.scalars(select(MailDelivery).where(MailDelivery.email == email,
            MailDelivery.status == "queued").order_by(MailDelivery.created_at.desc()).limit(10))
        for item in items:
            print(f"{item.created_at} {item.purpose}: {item.body}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email")
    asyncio.run(main(parser.parse_args().email))
