"""Conservative preflight reservations shared by all nested generation calls."""
from contextvars import ContextVar
from dataclasses import dataclass

from backend.agent_types import AgentState


class BudgetExceeded(ValueError):
    pass


@dataclass
class ModelScope:
    state: AgentState
    context: dict


model_scope: ContextVar[ModelScope | None] = ContextVar("agent_model_scope", default=None)


def reserve(serialized_request: str, max_output: int = 4096):
    from backend.context_budget import preflight
    return preflight(serialized_request, max_output)
