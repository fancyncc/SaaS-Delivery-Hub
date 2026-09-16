from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.config import get_settings
from backend.models import MailDelivery


async def send_account_link(email: str, purpose: str, url: str, *, session: AsyncSession | None = None) -> None:
    if get_settings().mail_debug:
        return
    if session is not None:
        session.add(MailDelivery(email=email, purpose=purpose, body=url))
        return
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine)() as own_session:
            own_session.add(MailDelivery(email=email, purpose=purpose, body=url))
            await own_session.commit()
    finally:
        await engine.dispose()


def smtp_send(item: MailDelivery) -> None:
    settings = get_settings()
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = item.email
    message["Subject"] = f"SaaS Implementation: {item.purpose}"
    message["Message-ID"] = f"<{item.id}@implementation.local>"
    message.set_content(f"请使用以下一次性链接完成操作：\n\n{item.body}\n\n如果不是本人请求，请忽略。")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)


async def flush_mail() -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            identifiers = list(await session.scalars(select(MailDelivery.id).where(MailDelivery.status == "queued").limit(30)))
        for identifier in identifiers:
            async with factory() as session:
                # Commit ownership BEFORE SMTP. A crash leaves an unresolved
                # attempt which must never be automatically resent.
                claimed = await session.execute(update(MailDelivery).where(
                    MailDelivery.id == identifier, MailDelivery.status == "queued",
                ).values(status="sending", attempts=MailDelivery.attempts + 1))
                if not claimed.rowcount:
                    continue
                await session.commit()
                item = await session.get(MailDelivery, identifier)
                try:
                    await asyncio.to_thread(smtp_send, item)
                    item.status, item.body = "sent", ""
                except Exception as exc:
                    # SMTP can accept DATA before the connection fails. Neither
                    # an exception nor Message-ID proves delivery did not occur.
                    item.last_error = type(exc).__name__
                    item.status, item.body = "unknown", ""
                await session.commit()
    finally:
        await engine.dispose()
