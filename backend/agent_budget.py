"""Conservative preflight reservations shared by all nested generation calls."""
from contextvars import ContextVar
from dataclasses import dataclass

from backend.agent_types import AgentState
from backend.config import get_settings


class BudgetExceeded(ValueError):
    pass


@dataclass
class ModelScope:
    state: AgentState
    context: dict


model_scope: ContextVar[ModelScope | None] = ContextVar("agent_model_scope", default=None)


def reserve(serialized_request: str, max_output: int = 4096):
    scope = model_scope.get()
    if scope is None:
        return
    # UTF-8 bytes upper-bound text tokenizer input usage. This is explicitly a
    # reservation, not a claim of measured provider usage or monetary cost.
    reservation = len(serialized_request.encode("utf-8")) + max_output
    if scope.state.tokens_reserved + reservation > get_settings().agent_token_budget:
        raise BudgetExceeded("模型预算不足，请人工检查后恢复")
    scope.state.tokens_reserved += reservation
