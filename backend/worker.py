"""Celery delivery with DB-owned checkpoints and replay-safe node transactions."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TypedDict

from celery import Celery
from fastapi import HTTPException
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.access import require_project_permission
from backend.config import get_settings
from backend.models import AgentRun, Project, Tenant, TenantMembership, User, WorkflowOutbox
from backend.schemas import Role
from backend.security import Principal
from backend.state_machine import ProjectLifecycle, RunLifecycle, transition_project, transition_run
from backend.workflow import NODES, advance

celery = Celery("implementation", broker=get_settings().redis_url)
celery.conf.update(
    task_acks_late=True, task_reject_on_worker_lost=True, worker_prefetch_multiplier=1,
    task_soft_time_limit=240, task_time_limit=300,
    task_routes={"implementation.knowledge": {"queue": "indexing"}},
    beat_schedule={"chat_context": {"task": "implementation.chat_context", "schedule": 5.0}, "outbox": {"task": "implementation.dispatch", "schedule": 2.0}, "mail": {"task": "implementation.mail", "schedule": 10.0}, "knowledge": {"task": "implementation.knowledge", "schedule": 10.0}},
)


async def actor_context(session, event):
    membership = await session.scalar(select(TenantMembership).where(TenantMembership.user_id == event.actor_id, TenantMembership.tenant_id == event.tenant_id, TenantMembership.status == "active"))
    user = await session.get(User, event.actor_id)
    tenant = await session.get(Tenant, event.tenant_id)
    if not membership or not user or user.status != "active" or not tenant or tenant.status != "active":
        raise HTTPException(403, "执行人的公司授权已失效")
    if session.bind.dialect.name == "postgresql":
        for key, value in {"app.current_user_id": user.id, "app.current_tenant_id": event.tenant_id, "app.current_company_role": membership.company_role_code, "app.platform_roles": "", "app.is_platform_admin": "false"}.items():
            await session.execute(text("SELECT set_config(:key, :value, true)"), {"key": key, "value": value})
    return Principal(user_id=user.id, subject=user.email or user.username, email=user.email or "", username=user.username, display_name=user.display_name, tenant_id=tenant.id, tenant_name=tenant.name, account_type=user.account_type, session_context="customer", role=Role.TENANT_ADMIN if membership.company_role_code == "company_admin" else Role.TENANT_MEMBER, is_platform_admin=False, company_role_code=membership.company_role_code, platform_roles=(), session_id="worker")


class Cursor(TypedDict):
    next: str


def next_node(run: AgentRun) -> str:
    if run.status == "pending":
        return NODES[0]
    if run.status != "running":
        return END
    completed = run.state.get("completed_nodes", [])
    return next((node for node in NODES if node not in completed), END)


async def process_event(event_id: str, *, checkpointer=None, raise_errors: bool = False) -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as owner:
            # Held across the graph: duplicate deliveries cannot interleave.
            event = await owner.scalar(select(WorkflowOutbox).where(WorkflowOutbox.id == event_id).with_for_update(skip_locked=True))
            if not event or event.processed:
                return

            async def step(_: Cursor) -> Cursor:
                async with factory() as session:
                    principal = await actor_context(session, event)
                    run = await session.scalar(select(AgentRun).where(AgentRun.id == event.run_id).with_for_update())
                    if not run:
                        raise HTTPException(403, "项目访问权限已撤销")
                    if run.status == "pending":
                        transition_run(run, RunLifecycle.RUNNING)
                        run.state = {**run.state, "status": "running"}
                    if run.status != "running":
                        return {"next": END}
                    project = await session.get(Project, run.project_id)
                    if project is None or project.deleted_at:
                        transition_run(run, RunLifecycle.CANCELLED)
                        run.state = {**run.state, "status": "cancelled", "blocking_reason": "项目已删除"}
                    else:
                        await require_project_permission(session, project, principal, "run.start")
                        await advance(session, run, one_node=True)
                    await session.commit()
                    return {"next": next_node(run)}

            try:
                async with factory() as session:
                    await actor_context(session, event)
                    run = await session.get(AgentRun, event.run_id)
                    if not run:
                        raise HTTPException(403, "项目访问权限已撤销")
                    initial = next_node(run)
                graph = StateGraph(Cursor)
                for node in NODES:
                    graph.add_node(node, RunnableLambda(step))
                    graph.add_conditional_edges(node, lambda cursor: cursor["next"])
                graph.add_conditional_edges(START, lambda cursor: cursor["next"])
                compiled = graph.compile(checkpointer=checkpointer or InMemorySaver())
                await compiled.ainvoke({"next": initial}, {"configurable": {"thread_id": event.id}, "recursion_limit": 600}, durability="sync")
                event.processed = True
                event.last_error = ""
            except Exception as exc:
                if raise_errors:
                    raise
                event.attempts += 1
                event.last_error = type(exc).__name__  # never persist credential-bearing exception strings
                event.next_attempt_at = datetime.now(UTC) + timedelta(seconds=2 ** event.attempts)
                terminal = isinstance(exc, HTTPException) or event.attempts >= 3
                if terminal:
                    event.processed = True
                    async with factory() as session:
                        try:
                            await actor_context(session, event)
                            run = await session.scalar(select(AgentRun).where(AgentRun.id == event.run_id).with_for_update())
                            if run and run.status in {"pending", "running"}:
                                if run.status == "pending":
                                    transition_run(run, RunLifecycle.RUNNING)
                                transition_run(run, RunLifecycle.FAILED)
                                run.state = {**run.state, "status": "failed", "blocking_reason": f"执行失败：{event.last_error}；请检查依赖后创建整改 Run"}
                                project = await session.get(Project, run.project_id)
                                if project and project.lifecycle_status == "in_progress":
                                    transition_project(project, ProjectLifecycle.BLOCKED)
                                await session.commit()
                        except HTTPException:
                            # No privilege escalation when the original actor lost access.
                            # The outbox failure remains visible to platform operations.
                            pass
            await owner.commit()
    finally:
        await engine.dispose()


async def consume(event_id):
    if get_settings().database_url.startswith("postgresql"):
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        url = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        async with AsyncPostgresSaver.from_conn_string(url) as saver:
            await process_event(event_id, checkpointer=saver)
    else:
        await process_event(event_id)


@celery.task(name="implementation.run")
def execute(event_id: str):
    asyncio.run(consume(event_id))


async def publish():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine)
    try:
        async with factory() as session:
            events = (await session.scalars(select(WorkflowOutbox).where(WorkflowOutbox.processed.is_(False), WorkflowOutbox.next_attempt_at <= datetime.now(UTC)).limit(100).with_for_update(skip_locked=True))).all()
            for event in events:
                execute.delay(event.id)
                # A lost message is republished. An active consumer owns a row lock.
                event.next_attempt_at = datetime.now(UTC) + timedelta(seconds=60)
            await session.commit()
    finally:
        await engine.dispose()


@celery.task(name="implementation.dispatch")
def dispatch_outbox():
    asyncio.run(publish())


@celery.task(name="implementation.mail")
def deliver_mail():
    from backend.mailer import flush_mail
    asyncio.run(flush_mail())


async def setup_checkpoints():
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    from backend.db_role import owner_url
    async with AsyncPostgresSaver.from_conn_string(owner_url()) as saver:
        await saver.setup()


async def index_knowledge():
    from backend.knowledge import process_pending
    from backend.retrieval_sources import process_sources
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            tenants = list(await session.scalars(select(Tenant.id).where(Tenant.status == "active")))
        for tenant_id in tenants:
            async with factory() as session:
                if session.bind.dialect.name == "postgresql":
                    await session.execute(text("SELECT set_config('app.current_tenant_id', :tenant, true)"), {"tenant": tenant_id})
                    # Tenant indexing is a service operation over all registered
                    # origins; end-user visibility is checked at retrieval time.
                    await session.execute(text("SELECT set_config('app.current_company_role', 'company_admin', true)"))
                if get_settings().rag_mode == "real":
                    await process_sources(session, tenant_id)
                else:
                    await process_pending(session, tenant_id)
                if get_settings().rag_v3_indexing_enabled:
                    from backend.rag_v3_index import process
                    await process(session, tenant_id)
                await session.commit()
    finally:
        await engine.dispose()


@celery.task(name="implementation.knowledge")
def build_knowledge_indexes():
    asyncio.run(index_knowledge())


if __name__ == "__main__":
    asyncio.run(setup_checkpoints())


@celery.task(name="implementation.chat_context")
def build_chat_context():
    from backend.context_maintenance import maintain_pending
    asyncio.run(maintain_pending())
