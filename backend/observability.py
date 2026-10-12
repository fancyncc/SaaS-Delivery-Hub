import re
import secrets
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from time import monotonic
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import NonRecordingSpan, SpanContext, Status, StatusCode, TraceFlags
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from prometheus_client import Counter, Histogram

from backend.config import get_settings

REQUESTS = Counter("saas_api_requests_total", "API requests", ["method", "route", "status"])
LATENCY = Histogram("saas_api_seconds", "API latency", ["method", "route"])
_provider: TracerProvider | None = None
_exporter_configured = False
_measurement: ContextVar[dict[str, Any] | None] = ContextVar("execution_span", default=None)


def valid_parent(value: str | None) -> bool:
    match = re.fullmatch(r"00-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}", value or "")
    return bool(match and int(match[1], 16) and int(match[2], 16))


def trace_identifier(traceparent: str | None) -> str:
    """Accept valid version-00 parent IDs; never persist malformed client text."""
    match = re.fullmatch(r"00-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}", traceparent or "")
    if match and int(match[1], 16) and int(match[2], 16):
        return match[1]
    return secrets.token_hex(16)


def setup(service_name: str = "saas-implementation.api") -> TracerProvider:
    global _provider, _exporter_configured
    if _provider is None:
        _provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    endpoint = get_settings().otel_exporter_otlp_endpoint
    if endpoint and not _exporter_configured:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint.rstrip("/") + "/v1/traces")))
        _exporter_configured = True
    return _provider


def context_from(carrier: dict):
    value = carrier.get("traceparent")
    return TraceContextTextMapPropagator().extract({"traceparent": value}) if valid_parent(value) else None


def trace_carrier() -> dict:
    result: dict[str, str] = {}
    TraceContextTextMapPropagator().inject(result)
    return {"traceparent": result["traceparent"]} if valid_parent(result.get("traceparent")) else {}


@contextmanager
def span(name: str, *, parent: dict | None = None, trace_id: str | None = None, **attributes):
    context = context_from(parent) if parent else None
    if context is None and not trace.get_current_span().get_span_context().is_valid:
        if trace_id and re.fullmatch(r"[0-9a-f]{32}", trace_id) and int(trace_id, 16):
            seed = SpanContext(int(trace_id, 16), int(secrets.token_hex(8), 16) or 1,
                               is_remote=True, trace_flags=TraceFlags(1))
            context = trace.set_span_in_context(NonRecordingSpan(seed))
    tracer = setup().get_tracer("implementation")
    with tracer.start_as_current_span(name, context=context, attributes=attributes,
                                     record_exception=False, set_status_on_exception=False) as active:
        identifier = active.get_span_context()
        measurement: dict[str, Any] = {"trace_id": f"{identifier.trace_id:032x}", "span_id": f"{identifier.span_id:016x}",
                       "started_at": datetime.now(UTC).isoformat(), "_started": monotonic()}
        token = _measurement.set(measurement)
        try:
            yield active
        except BaseException as exc:
            active.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            active.set_attribute("error.type", type(exc).__name__)
            raise
        finally:
            active.set_attribute("duration_ms", round((monotonic() - measurement["_started"]) * 1000, 3))
            _measurement.reset(token)


def span_measurement() -> dict:
    value = _measurement.get()
    if not value:
        return {}
    return {**{key: item for key, item in value.items() if key != "_started"},
            "duration_ms": round((monotonic() - value["_started"]) * 1000, 3)}


async def instrument(request, call_next):
    started = monotonic()
    status = "500"
    try:
        with span("http.request", parent={"traceparent": request.headers.get("traceparent")},
                  trace_id=getattr(request.state, "trace_id", None), method=request.method) as active:
            request.state.trace_id = f"{active.get_span_context().trace_id:032x}"
            response = await call_next(request)
            status = str(response.status_code)
            active.set_attribute("http.response.status_code", response.status_code)
            response.headers["X-Trace-ID"] = request.state.trace_id
            return response
    finally:
        route = getattr(request.scope.get("route"), "path", "unmatched")
        REQUESTS.labels(request.method, route, status).inc()
        LATENCY.labels(request.method, route).observe(monotonic() - started)
