"""DB-owned, leased, coalescing context maintenance for Celery and local mode."""
import asyncio
import logging
import time
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
from prometheus_client import Counter, Histogram
from sqlalchemy import case, or_, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.chat_models import ChatContextSnapshot, ChatContextTask, ChatConversation
from backend.config import get_settings
from backend.context_budget import count_tokens, input_limit
from backend.conversation_state import (
    EXTRACTIVE_FALLBACK,
    digest,
    source_digest,
    summarize,
    update_state,
    user_messages,
)

JOBS = Counter("saas_context_jobs_total", "Context maintenance outcomes", ["outcome"])
LAG = Histogram("saas_context_lag_turns", "Unprocessed conversation turns", buckets=(0, 1, 2, 4, 8, 16, 32, 64, 100, 150, 200))


async def enqueue(session, row, target_version):
    if get_settings().chat_context_mode == "off":
        return
    # Atomic upsert also covers two SQLite requests lazily discovering the same
    # old conversation. Preserve a live lease; completion sees the newer target.
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    running = ChatContextTask.status == "running"
    statement = insert(ChatContextTask).values(conversation_id=row.id, tenant_id=row.tenant_id,
        user_id=row.user_id, target_version=target_version)
    statement = statement.on_conflict_do_update(index_elements=["conversation_id"], set_={
        "target_version": target_version,
        "status": case((running, ChatContextTask.status), else_="pending"),
        "next_attempt": case((running, ChatContextTask.next_attempt), else_=0),
        "attempts": case((running, ChatContextTask.attempts), else_=0),
        "error_code": case((running, ChatContextTask.error_code), else_=""),
    }, where=ChatContextTask.target_version <= target_version)
    await session.execute(statement)


async def bootstrap_context(session, row, expected_version):
    """Lazy backfill old committed history even if the next answer cannot fit."""
    if get_settings().chat_context_mode == "off" or not row.messages:
        return
    from fastapi import HTTPException
    locked = await session.scalar(select(ChatConversation).where(ChatConversation.id == row.id)
        .execution_options(populate_existing=True).with_for_update())
    if locked is None or locked.version != expected_version:
        raise HTTPException(409, "对话已更新，请刷新后重试")
    from backend.chat_history import hydrate
    await hydrate(session, row)
    task = await session.get(ChatContextTask, row.id)
    snapshot = await session.get(ChatContextSnapshot, row.id)
    if task is None or not valid_snapshot(snapshot, row) or snapshot.processed_turn < len(row.messages):
        await enqueue(session, row, row.version)
    await session.commit()
    await hydrate(session, row)


def valid_snapshot(snapshot, row):
    return bool(snapshot and snapshot.user_id == row.user_id and snapshot.tenant_id == row.tenant_id
        and snapshot.producer_version == "context-v1"
        and snapshot.processed_turn <= len(row.messages)
        and snapshot.source_digest == source_digest(row.messages[:snapshot.processed_turn])
        and snapshot.documents_digest == digest(row.documents))


async def maintain_one(session, identifier):
    now, lease = time.time(), str(uuid4())
    lease_seconds = max(300, get_settings().model_timeout * 6 + 60)
    claimed = await session.execute(update(ChatContextTask).where(
        ChatContextTask.conversation_id == identifier,
        ChatContextTask.next_attempt <= now,
        or_(ChatContextTask.status == "pending", (ChatContextTask.status == "running") & (ChatContextTask.lease_until < now)),
    ).values(status="running", lease_token=lease, lease_until=now + lease_seconds,
        attempts=ChatContextTask.attempts + 1))
    await session.commit()
    if claimed.rowcount != 1:
        return False
    try:
        from backend.chat_routes import owned
        from backend.worker import actor_context
        task = await session.get(ChatContextTask, identifier, populate_existing=True)
        if task is None:
            return False
        user = await actor_context(session, SimpleNamespace(actor_id=task.user_id, tenant_id=task.tenant_id))
        row = await owned(session, identifier, user)
        snapshot = await session.get(ChatContextSnapshot, identifier, populate_existing=True)
        valid = valid_snapshot(snapshot, row)
        start = snapshot.processed_turn if valid else 0
        target = min(start + 8, len(row.messages))
        old_state = snapshot.state if valid else {}
        summary = snapshot.summary if valid else []
        covered = snapshot.summary_until if valid else 0
        originals = user_messages(row.messages)
        prefix_digest = source_digest(row.messages[:target])
        documents_digest = digest(row.documents)
        version = row.version
        EXTRACTIVE_FALLBACK.set(False)
        state = await update_state(old_state, originals[start:target]) if target > start else old_state
        boundary = max(0, target - 8)
        pressure = count_tokens({"state": state, "recent": originals[covered:target], "summary": summary}).tokens > input_limit() * 0.8
        if boundary > covered and (boundary - covered >= 8 or pressure):
            summary = await summarize(summary, originals[covered:boundary], state)
            covered = boundary
        # Re-read after generation; ownership, deletion, source revisions and lease
        # are checked again. Lock ordering matches chat writes (conversation first).
        session.expire_all()
        user = await actor_context(session, SimpleNamespace(actor_id=user.user_id, tenant_id=user.tenant_id))
        row = await session.scalar(select(ChatConversation).where(ChatConversation.id == identifier).with_for_update())
        if row is None:
            await session.rollback()
            return False
        row = await owned(session, identifier, user)
        task = await session.scalar(select(ChatContextTask).where(ChatContextTask.conversation_id == identifier).with_for_update())
        if task is None or task.lease_token != lease or task.lease_until < time.time():
            await session.rollback()
            return False
        # Conditional write also serializes completion on SQLite, which ignores
        # SELECT FOR UPDATE. A stolen/expired lease cannot publish a snapshot.
        fenced = await session.execute(update(ChatContextTask).where(
            ChatContextTask.conversation_id == identifier, ChatContextTask.lease_token == lease,
            ChatContextTask.lease_until >= time.time()).values(lease_until=time.time() + 60))
        if fenced.rowcount != 1:
            await session.rollback()
            return False
        if source_digest(row.messages[:target]) != prefix_digest or digest(row.documents) != documents_digest:
            task.status, task.lease_until = "pending", 0
            await session.commit()
            return False
        from backend.chat_history import hydrate, index_history
        await hydrate(session, row)
        snapshot = await session.get(ChatContextSnapshot, identifier)
        if snapshot is not None and valid_snapshot(snapshot, row) and snapshot.processed_turn > target:
            task.status, task.lease_until = "pending", 0
            await session.commit()
            return False
        if snapshot is None:
            snapshot = ChatContextSnapshot(conversation_id=identifier, tenant_id=row.tenant_id, user_id=row.user_id, generation=0)
            session.add(snapshot)
        from backend.memory_items import extract_candidates
        await extract_candidates(session, row, originals[start:target])
        snapshot.processed_turn, snapshot.summary_until = target, covered
        snapshot.source_digest, snapshot.documents_digest = prefix_digest, documents_digest
        snapshot.state, snapshot.summary = state, summary
        snapshot.generation += 1
        snapshot.mode = "model" if get_settings().model_mode == "real" and not EXTRACTIVE_FALLBACK.get() else "extractive"
        snapshot.producer_version = "context-v1"
        task.status = "pending" if target < len(row.messages) or task.target_version != version else "done"
        task.attempts, task.next_attempt, task.lease_until, task.error_code = 0, 0, 0, ""
        await session.commit()
        JOBS.labels("succeeded").inc()
        # Vector inference runs after releasing conversation/task locks.
        try:
            await index_history(session, row)
            await session.commit()
        except Exception:
            await session.rollback()
        return True
    except Exception as exc:
        await session.rollback()
        # Never log model/source text, exception bodies or private identifiers.
        logging.getLogger(__name__).warning("Context maintenance failed (%s, status=%s); durable retry scheduled",
            type(exc).__name__, exc.status_code if isinstance(exc, HTTPException) else "n/a")
        task = await session.get(ChatContextTask, identifier, populate_existing=True)
        if task and task.lease_token == lease:
            await session.execute(update(ChatContextTask).where(
                ChatContextTask.conversation_id == identifier, ChatContextTask.lease_token == lease,
            ).values(status="failed" if task.attempts >= 5 else "pending",
                error_code="context_update_failed", next_attempt=time.time() + min(300, 2 ** task.attempts * 5), lease_until=0))
            await session.commit()
        JOBS.labels("failed").inc()
        return False


async def maintain_pending():
    if get_settings().chat_context_mode == "off":
        return
    from sqlalchemy import text

    from backend.models import Tenant
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            tenants = list(await session.scalars(select(Tenant.id).where(Tenant.status == "active")))
        for tenant in tenants:
            async with factory() as session:
                session.info["rls_context"] = {"app.current_tenant_id": tenant}
                if session.bind.dialect.name == "postgresql":
                    await session.execute(text("SELECT set_config('app.current_tenant_id', :tenant, true)"), {"tenant": tenant})
                identifiers = list(await session.scalars(select(ChatContextTask.conversation_id).where(
                    ChatContextTask.tenant_id == tenant, ChatContextTask.status.in_(["pending", "running"]),
                    ChatContextTask.next_attempt <= time.time()).order_by(ChatContextTask.next_attempt).limit(20)))
                for identifier in identifiers:
                    await maintain_one(session, identifier)
    finally:
        await engine.dispose()


async def maintain_local():
    while True:
        try:
            await maintain_pending()
        except Exception:
            logging.getLogger(__name__).warning("Context maintenance pass unavailable")
        await asyncio.sleep(5)
