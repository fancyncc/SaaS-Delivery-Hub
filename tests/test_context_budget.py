import json

import httpx
import pytest
from fastapi import HTTPException

from backend.agent_budget import BudgetExceeded, ModelScope, model_scope
from backend.agent_types import AgentState
from backend.config import get_settings
from backend.context_budget import ContextBlock, ContextBundle, count_tokens, input_limit, preflight
from backend.llm_stream import stream_text


def test_explicit_tokenizer_mapping_and_fallback(monkeypatch):
    import backend.context_budget as budget
    settings = get_settings()
    monkeypatch.setattr(settings, "model_name", "gateway-alias")
    monkeypatch.setattr(settings, "llm_mode", "online")
    monkeypatch.setattr(settings, "model_tokenizer_map", {"gateway-alias": "tiktoken:test"})
    seen = []

    class Encoder:
        def encode(self, text, **kwargs):
            seen.append(text)
            return [1, 2, 3]

    def mapped(spec):
        assert spec == "tiktoken:test"
        return Encoder()

    monkeypatch.setattr(budget, "tokenizer", mapped)
    assert count_tokens("中文 English").tokens == 3
    assert count_tokens("中文 English").method == "tokenizer"
    assert seen
    monkeypatch.setattr(budget, "tokenizer", lambda spec: None)
    assert count_tokens("中文 English").method == "estimated"
    assert count_tokens("中文 English").estimated


def test_full_envelope_limit_and_required_blocks(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "chat_context_tokens", 1000)
    with pytest.raises(HTTPException, match="422"):
        preflight({"messages": [], "tools": [{"description": "x" * 2000}], "max_tokens": 128})
    bundle = ContextBundle([ContextBlock("history", "x" * 400, "old", "advisory"),
        ContextBlock("question", "current", "user", "required", True)])
    bundle.select(100)
    assert bundle.blocks[1].selected
    assert not bundle.blocks[0].selected
    assert bundle.blocks[0].reason == "token_budget"
    with pytest.raises(HTTPException):
        ContextBundle([ContextBlock("question", "x" * 200, "user", "required", True)]).select(10)


def test_window_output_and_margin(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "model_context_window", 10000)
    monkeypatch.setattr(s, "model_max_output_tokens", 2000)
    monkeypatch.setattr(s, "context_safety_ratio", 0.1)
    assert input_limit() == 7000


def test_accounting_retries_missing_usage_and_recovery(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "agent_token_budget", 20000)
    state = AgentState()
    token = model_scope.set(ModelScope(state, {}))
    try:
        failed = preflight({"messages": [], "max_tokens": 100})
        held = state.tokens_reserved
        failed.settle(None)
        assert state.tokens_reserved == held
        retry = preflight({"messages": [], "max_tokens": 100})
        retry.settle({"prompt_tokens": 10, "completion_tokens": 5})
        assert state.tokens_reserved == held + 15
        assert state.tokens_actual == 15
        assert state.tokens_unsettled == held
        assert state.tokens_estimated == failed.input_tokens + retry.input_tokens
        retry.settle({"prompt_tokens": 10, "completion_tokens": 5})
        assert state.tokens_reserved == held + 15
        restored = AgentState.model_validate(state.model_dump())
        assert restored.tokens_reserved == state.tokens_reserved
        monkeypatch.setattr(s, "agent_token_budget", state.tokens_reserved)
        with pytest.raises(BudgetExceeded):
            preflight({"messages": [], "max_tokens": 100})
    finally:
        model_scope.reset(token)


@pytest.mark.parametrize("with_usage", [True, False])
async def test_stream_usage_settlement(monkeypatch, with_usage):
    s = get_settings()
    for key, value in {"model_api_style": "chat_completions", "llm_mode": "online",
                       "model_name": "test", "model_base_url": "https://example.test", "model_api_key": "test"}.items():
        monkeypatch.setattr(s, key, value)
    state = AgentState()
    token = model_scope.set(ModelScope(state, {}))

    def handler(request):
        assert json.loads(request.content)["stream_options"]["include_usage"]
        events = [{"choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}]}]
        if with_usage:
            events.append({"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 2}})
        return httpx.Response(200, text="".join("data: " + json.dumps(e) + "\n\n" for e in events) + "data: [DONE]\n\n")

    try:
        assert "".join([v async for v in stream_text("test", {}, transport=httpx.MockTransport(handler))]) == "ok"
        assert state.tokens_reserved == 22 if with_usage else state.tokens_reserved > 22
    finally:
        model_scope.reset(token)


def test_context_scoring_shadow_and_evidence_boundary(monkeypatch):
    settings = get_settings()
    def bundle():
        return ContextBundle([
            ContextBlock("memory", "old", "memory", "advisory", relevance=0, recency=0, task_value=0),
            ContextBlock("history", "new", "history", "user_statement", relevance=1, recency=1, task_value=1),
        ])
    monkeypatch.setattr(settings, "context_scoring_mode", "shadow")
    shadow = bundle().select(12)
    assert shadow.blocks[0].selected and not shadow.blocks[1].selected
    monkeypatch.setattr(settings, "context_scoring_mode", "on")
    scored = bundle().select(12)
    assert scored.blocks[1].selected and not scored.blocks[0].selected
    assert scored.blocks[1].score() == pytest.approx(0.94)
    evidence = ContextBundle([ContextBlock("history", "old", "history", "advisory", relevance=1, recency=1, task_value=1),
        ContextBlock("evidence", "now", "runtime", "evidence", relevance=0, recency=0, task_value=0)]).select(12)
    assert evidence.blocks[1].selected and not evidence.blocks[0].selected
