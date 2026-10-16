from backend import chat


async def test_chat_numbers_evidence_and_retries_mismatched_citations(monkeypatch):
    settings = chat.get_settings()
    monkeypatch.setattr(settings, "rag_mode", "real")
    monkeypatch.setattr(settings, "model_mode", "real")
    attempts = []

    async def structured(instructions, payload, schema):
        attempts.append((instructions, payload))
        assert [item["number"] for item in payload["evidence"]] == [1, 2]
        if len(attempts) == 1:
            return chat.ChatAnswer(answer="依据见 [2]。", citation_ids=[payload["evidence"][0]["id"]])
        return chat.ChatAnswer(answer="依据见 [2]。", citation_ids=[payload["evidence"][1]["id"]])

    monkeypatch.setattr(chat.intelligence, "structured", structured)
    result = await chat.answer_question("期限是多少", [], "", [
        {"id": "first", "title": "无关资料", "text": "其他内容"},
        {"id": "second", "title": "SLA", "text": "首次响应 15 分钟"}])
    assert len(attempts) == 2
    assert [item["id"] for item in result["citations"]] == ["second"]


async def test_chunk_ordinal_cannot_be_confused_with_number_and_metadata_is_retained(monkeypatch):
    from copy import deepcopy

    monkeypatch.setattr(chat.get_settings(), "model_mode", "real")
    sources = [
        {"id": "document:4", "title": "First", "text": "先完成审批。", "document_id": "doc-1"},
        {"id": "document:3", "title": "Second", "text": "审批后执行导入。", "document_id": "doc-2",
         "version": 7, "location": {"page": 3}, "source_chunk_ids": ["document:3"]},
    ]
    original = deepcopy(sources)

    async def structured(instructions, payload, schema):
        assert [(s["number"], s["id"]) for s in payload["evidence"]] == [(1, "evidence-1"), (2, "evidence-2")]
        return chat.ChatAnswer(answer="审批后执行导入。[2]", citation_ids=["evidence-2"])

    monkeypatch.setattr(chat.intelligence, "structured", structured)
    result = await chat.answer_question("何时导入？", [], "", sources)
    assert sources == original
    assert result["citations"] == [dict(original[1], number=2)]


async def test_unknown_aliases_are_still_rejected_after_bounded_retry(monkeypatch):
    import pytest
    from fastapi import HTTPException

    monkeypatch.setattr(chat.get_settings(), "model_mode", "real")
    attempts = []

    async def invalid(instructions, payload, schema):
        attempts.append(payload)
        return chat.ChatAnswer(answer="不可信结论[1]", citation_ids=["evidence-99"])

    monkeypatch.setattr(chat.intelligence, "structured", invalid)
    with pytest.raises(HTTPException) as error:
        await chat.answer_question("测试", [], "", [{"id": "real:99", "title": "资料", "text": "合法原文"}])
    assert error.value.status_code == 422
    assert len(attempts) == 2
