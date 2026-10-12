"""Opt-in provider receipts for offline answer evaluation."""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

_receipts = ContextVar("evaluation_model_receipts", default=None)


@contextmanager
def capture_model_receipts():
    rows = []
    token = _receipts.set(rows)
    try:
        yield rows
    finally:
        _receipts.reset(token)


def record_model_receipt(body, output):
    rows = _receipts.get()
    if rows is not None:
        rows.append(deepcopy({"provider_model": body.get("model"),
                             "provider_response_id": body.get("id"),
                             "usage": body.get("usage"), "output": output}))


def measured_usage(receipts):
    total = {"input_tokens": 0, "output_tokens": 0}
    if not receipts:
        return None
    for row in receipts:
        usage = row.get("usage") or {}
        values = {"input_tokens": usage.get("input_tokens", usage.get("prompt_tokens")),
                  "output_tokens": usage.get("output_tokens", usage.get("completion_tokens"))}
        if any(type(v) is not int or v < 0 for v in values.values()):
            return None
        for key, value in values.items():
            total[key] += value
    return total
