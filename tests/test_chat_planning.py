import pytest

from backend import intelligence
from backend.chat_planning import QueryPlan, plan_query
from backend.config import get_settings


@pytest.mark.parametrize("question", [
    "这个平台有哪些功能，各自从哪里进入？",
    "这个平台的角色权限有哪些？",
    "这个系统怎么创建公司？",
])
async def test_explicit_platform_reference_does_not_require_clarification(monkeypatch, question):
    monkeypatch.setattr(get_settings(), "model_mode", "real")

    async def unnecessary_model_call(*args, **kwargs):
        raise AssertionError("An explicit platform reference must not be treated as an unresolved pronoun")

    monkeypatch.setattr(intelligence, "structured", unnecessary_model_call)
    result = await plan_query(question, [])
    assert result.action == "keep"
    assert result.query == question
    assert not result.clarification


@pytest.mark.parametrize("question", ["它怎么办", "那个平台有哪些功能？", "这个平台的那个问题怎么办？"])
async def test_unresolved_references_still_use_clarification(monkeypatch, question):
    monkeypatch.setattr(get_settings(), "model_mode", "real")

    async def clarify(task, source, schema):
        assert source["question"] == question
        return QueryPlan(action="clarify", query="", clarification="请说明具体对象。")

    monkeypatch.setattr(intelligence, "structured", clarify)
    result = await plan_query(question, [])
    assert result.action == "clarify"
    assert result.clarification == "请说明具体对象。"
