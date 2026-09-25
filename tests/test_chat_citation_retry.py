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
            return chat.ChatAnswer(answer="依据见 [2]。", citation_ids=["first"])
        return chat.ChatAnswer(answer="依据见 [2]。", citation_ids=["second"])

    monkeypatch.setattr(chat.intelligence, "structured", structured)
    result = await chat.answer_question("期限是多少", [], "", [
        {"id": "first", "title": "无关资料", "text": "其他内容"},
        {"id": "second", "title": "SLA", "text": "首次响应 15 分钟"}])
    assert len(attempts) == 2
    assert [item["id"] for item in result["citations"]] == ["second"]
