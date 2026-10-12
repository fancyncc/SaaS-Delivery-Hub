"""Opt-in stage snapshots for offline evaluation; normal requests incur no copies."""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

_trace = ContextVar("retrieval_evaluation_trace", default=None)


@contextmanager
def evaluation_trace():
    value = {stage: [] for stage in ("bm25", "vector", "rrf", "rerank", "relevance")}
    token = _trace.set(value)
    try:
        yield value
    finally:
        _trace.reset(token)


def enabled():
    return _trace.get() is not None


def capture(stage, hits):
    trace = _trace.get()
    if trace is not None:
        trace[stage].extend(deepcopy(hits))


def snapshot(allowed):
    trace = _trace.get()
    if trace is None:
        return {}
    return {stage: list({h["id"]: h for h in hits if h["document_id"] in allowed}.values())
            for stage, hits in trace.items()}
