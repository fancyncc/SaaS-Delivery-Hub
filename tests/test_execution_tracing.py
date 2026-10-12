import uuid

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import select
from test_api import project

from backend import observability
from backend.config import get_settings
from backend.db import SessionLocal
from backend.models import AgentStep, WorkflowOutbox
from backend.worker import process_event


@pytest.fixture
def spans(monkeypatch):
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(observability, "_provider", provider)
    yield exporter
    provider.shutdown()


async def test_api_outbox_worker_node_parent_chain(client, monkeypatch, spans):
    monkeypatch.setattr(get_settings(), "execution_mode", "worker")
    p = await project(client)
    trace_id = "c" * 32
    response = await client.post(f"/api/projects/{p['id']}/runs",
        headers={"Idempotency-Key": uuid.uuid4().hex, "traceparent": f"00-{trace_id}-{'d' * 16}-01"})
    assert response.status_code == 200
    run_id = response.json()["data"]["id"]
    async with SessionLocal() as session:
        event = await session.scalar(select(WorkflowOutbox).where(WorkflowOutbox.run_id == run_id))
        carrier = event.trace_context
        event_id = event.id
    assert carrier["traceparent"].split("-")[1] == trace_id
    await process_event(event_id, raise_errors=True)
    finished = spans.get_finished_spans()
    consumer = next(s for s in finished if s.name == "worker.consume")
    assert f"{consumer.context.trace_id:032x}" == trace_id
    assert f"{consumer.parent.span_id:016x}" == carrier["traceparent"].split("-")[2]
    nodes = [s for s in finished if s.name == "workflow.node"]
    assert len(nodes) == 6 and all(s.context.trace_id == consumer.context.trace_id for s in nodes)
    async with SessionLocal() as session:
        steps = list(await session.scalars(select(AgentStep).where(AgentStep.run_id == run_id)))
        assert all(s.detail["execution"]["duration_ms"] >= 0 for s in steps)
        business_steps = [s for s in steps if s.node != "agent_evaluation"]
        assert len(business_steps) == 6
        assert {s.detail["execution"]["span_id"] for s in business_steps} == {f"{s.context.span_id:016x}" for s in nodes}


def test_context_does_not_export_baggage_or_exception_secrets(spans):
    with pytest.raises(ValueError), observability.span("failure", parent={"traceparent": "invalid", "baggage": "password=secret"}):
        carrier = observability.trace_carrier()
        assert set(carrier) == {"traceparent"}
        raise ValueError("credential-bearing-secret")
    emitted = spans.get_finished_spans()[0]
    assert emitted.status.description == "ValueError"
    assert not emitted.events
    assert "secret" not in str(emitted.attributes)
