"""One authorized chat flow; transport controls only answer delivery."""
from datetime import UTC, datetime

from backend.chat import answer_instructions, answer_question, cited_sources
from backend.chat_planning import plan_query
from backend.config import get_settings


async def reply(session, row, user, payload, api_schema, memo):
    from backend.chat_context import build_context, context_status
    from backend.chat_mcp import authorized_conversation
    from backend.chat_routes import save
    from backend.chat_tools import tool_context

    question, steps = payload.question.strip(), []
    yield "step", {"stage": "plan", "summary": "正在判断问题是否需要补充上下文"}
    state = (await context_status(session, row))["state"] if get_settings().chat_context_mode == "on" else None
    plan = await plan_query(question, row.messages, state=state)
    steps.append({"stage": "plan", "action": plan.action, "query": plan.query})
    yield "step", steps[-1]
    if plan.action == "clarify":
        result = {"answer": plan.clarification, "citations": [], "mode": "clarification"}
        yield "delta", {"text": result["answer"]}
    else:
        yield "step", {"stage": "act", "summary": "正在检索授权资料和查询实时状态"}
        query = question if plan.action == "keep" else question + "\n" + plan.query
        sources = []
        async for observation in tool_context(session, row, user, query, api_schema):
            sources = observation["sources"]
            steps.append({"stage": "tool", "round": observation["round"],
                "tool": observation.get("tool", "search_authorized_context"),
                "summary": f"第 {observation['round']} 轮工具调用完成"})
            yield "step", steps[-1]
        steps.append({"stage": "observe", "source_count": len(sources),
            "retrieval": "hybrid" if get_settings().rag_mode == "real" else "keyword"})
        yield "step", steps[-1]
        context, memo, sources = await build_context(session, row, question, memo, sources)
        if payload.stream and get_settings().model_mode == "real" and not (sources and sources[0].get("kind") == "scope"):
            from backend.llm_stream import stream_text
            yield "step", {"stage": "answer", "summary": "正在根据本轮来源生成回答"}
            answer = ""
            async for delta in stream_text(answer_instructions() +
                    " 本次直接流式输出正文，不输出 JSON 或 citation_ids 字段。引用使用 evidence 顺序编号 [1]。",
                    {"question": question, "context": context, "memory": memo,
                     "evidence": [dict(source, number=i) for i, source in enumerate(sources, 1)]}):
                answer += delta
                yield "delta", {"text": delta}
            result = {"answer": answer, "citations": cited_sources(answer, sources), "mode": "llm"}
        else:
            result = await answer_question(question, row.messages, memo, sources, context=context)
            yield "delta", {"text": result["answer"]}
    await authorized_conversation(session, row.id, user, version=payload.expected_version)
    message = {"request_id": str(payload.request_id), "question": question,
        "created_at": datetime.now(UTC).isoformat(), "steps": steps, **result}
    saved = await save(session, row, payload.expected_version, messages=[*row.messages, message],
        title=row.title if row.messages or row.title != "新对话" else question[:120])
    yield "done", saved
