"""Generation-model token accounting and auditable context selection.

Tokenizer mapping is explicit: embedding tokenizers are never used here.
Protocol framing is provider-specific, so even tokenizer counts are preflight
estimates; only provider usage is labelled measured.
"""
import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from fastapi import HTTPException
from prometheus_client import Counter, Histogram

from backend.config import get_settings

TOKEN_BUCKETS = (0, 32, 128, 512, 1024, 2048, 4096, 8192, 12000, 16384, 32768, 65536, 131072)
TOKENS = Histogram("saas_context_tokens", "Token estimates and measured usage", ["kind"], buckets=TOKEN_BUCKETS)
TRIMMED = Counter("saas_context_trimmed_total", "Dropped context blocks", ["category"])
LAYERS = Histogram("saas_context_layer_tokens", "Per-layer context tokens", ["category", "selected"], buckets=TOKEN_BUCKETS)
SCORING = Histogram("saas_context_scoring_difference", "Blocks differing from priority selection", buckets=(0, 1, 2, 4, 8, 16, 32))


def serialized(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@lru_cache(maxsize=16)
def tokenizer(spec):
    try:
        if spec.startswith("tiktoken:"):
            import tiktoken
            return tiktoken.get_encoding(spec.split(":", 1)[1])
        if spec.startswith("hf:"):
            from transformers import AutoTokenizer
            return AutoTokenizer.from_pretrained(spec[3:], local_files_only=True, trust_remote_code=False)
    except (ImportError, OSError, ValueError):
        # Cache unavailable configurations too: no repeated loads per context block.
        return None
    return None


@dataclass(frozen=True)
class TokenCount:
    tokens: int
    method: str
    estimated: bool = True


def count_tokens(value: Any) -> TokenCount:
    s = get_settings()
    model = s.llm_local_model if s.llm_mode == "local" else s.model_name
    spec = s.model_tokenizer_map.get(model, s.model_tokenizer)
    text = value if isinstance(value, str) else serialized(value)
    try:
        encoder = tokenizer(spec)
        if encoder is not None:
            ids = encoder.encode(text, disallowed_special=()) if spec.startswith("tiktoken:") else encoder.encode(text, add_special_tokens=False)
            return TokenCount(len(ids), "tokenizer")
    except (ImportError, OSError, ValueError):
        pass
    # Unknown tokenizers: deliberately conservative Unicode estimate, not bytes.
    # ASCII <= one token/codepoint, non-ASCII <= four; safety margin is separate.
    return TokenCount(sum(1 if ord(c) < 128 else 4 for c in text), "estimated")


def input_limit(max_output=None, *, agent=False):
    s = get_settings()
    output = s.model_max_output_tokens if max_output is None else max_output
    return max(0, min(s.agent_context_tokens if agent else s.chat_context_tokens,
        s.model_context_window - output - math.ceil(s.model_context_window * s.context_safety_ratio)))


@dataclass
class Reservation:
    input_tokens: int
    output_tokens: int
    method: str
    scope: Any = None
    settled: bool = False

    def settle(self, usage):
        if self.settled or not isinstance(usage, dict):
            return
        incoming = usage.get("input_tokens", usage.get("prompt_tokens"))
        outgoing = usage.get("output_tokens", usage.get("completion_tokens"))
        if not all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in (incoming, outgoing)):
            return
        self.settled = True
        TOKENS.labels("actual_input").observe(incoming)
        TOKENS.labels("actual_output").observe(outgoing)
        TOKENS.labels("absolute_error").observe(abs(incoming - self.input_tokens))
        if self.scope:
            self.scope.state.tokens_reserved += incoming + outgoing - self.input_tokens - self.output_tokens
            self.scope.state.tokens_actual += incoming + outgoing
            self.scope.state.tokens_unsettled -= self.input_tokens + self.output_tokens


def preflight(payload, max_output=None):
    from backend.agent_budget import BudgetExceeded, model_scope
    s = get_settings()
    output = max_output if max_output is not None else payload.get("max_output_tokens", payload.get("max_tokens", s.model_max_output_tokens))
    # Counting the entire serialized envelope includes schemas, tools and roles;
    # fixed framing allowance covers provider-added delimiters.
    count = count_tokens(payload)
    incoming = count.tokens + 32
    scope = model_scope.get()
    if incoming > input_limit(output, agent=bool(scope)):
        message = "上下文超过模型 token 预算，请等待会话压缩完成、缩小输入或调整模型预算"
        if scope:
            raise BudgetExceeded(message)
        raise HTTPException(422, message)
    reservation = Reservation(incoming, output, count.method, scope)
    if scope:
        if scope.state.tokens_reserved + incoming + output > s.agent_token_budget:
            raise BudgetExceeded("模型预算不足，请人工检查后恢复")
        scope.state.tokens_reserved += incoming + output
        scope.state.tokens_estimated += incoming
        scope.state.tokens_unsettled += incoming + output
    TOKENS.labels(count.method).observe(incoming)
    TOKENS.labels("reserved").observe(incoming + output)
    return reservation


@dataclass
class ContextBlock:
    category: str
    value: Any
    source: str
    trust: str
    required: bool = False
    tokens: int = 0
    selected: bool = False
    reason: str = ""
    relevance: float = 0.5
    recency: float = 0.5
    task_value: float = 0.5

    def score(self):
        trust = {"required": 1, "evidence": 1, "user_statement": 0.8, "derived": 0.4, "advisory": 0.2}.get(self.trust, 0.2)
        return 0.4 * self.relevance + 0.3 * trust + 0.15 * self.recency + 0.15 * self.task_value


@dataclass
class ContextBundle:
    blocks: list[ContextBlock] = field(default_factory=list)

    def select(self, budget):
        # Callers provide deterministic priority order; required blocks reserve first.
        remaining = budget
        ordered = sorted(self.blocks, key=lambda b: not b.required)
        mode = get_settings().context_scoring_mode
        if mode != "off":
            for block in self.blocks:
                block.tokens = count_tokens(block.value).tokens + 8
            scored = sorted(self.blocks, key=lambda b: (not b.required,
                b.category not in {"evidence", "knowledge"}, -b.score(), b.tokens))
            def chosen(order):
                left, ids = budget, set()
                for b in order:
                    if b.tokens <= left:
                        ids.add(id(b))
                        left -= b.tokens
                return ids
            SCORING.observe(len(chosen(ordered) ^ chosen(scored)))
            if mode == "on":
                ordered = scored
        for block in ordered:
            block.selected = False
            block.tokens = count_tokens(block.value).tokens + 8
            if block.tokens <= remaining:
                block.selected, block.reason = True, "included"
                remaining -= block.tokens
            elif block.required:
                raise HTTPException(422, "尚未压缩的对话或必要信息超过预算，请等待压缩完成或缩小输入")
            else:
                block.reason = "token_budget"
                TRIMMED.labels(block.category).inc()
            LAYERS.labels(block.category, str(block.selected).lower()).observe(block.tokens)
        return self

    def report(self):
        # No content, identifiers or private text in telemetry.
        return [{"category": b.category, "trust": b.trust, "tokens": b.tokens,
                 "selected": b.selected, "reason": b.reason} for b in self.blocks]
