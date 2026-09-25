"""SSE orchestration: decide, retrieve, observe, stream, validate, commit."""
import json
import logging
import re
from datetime import UTC, datetime

from fastapi import HTTPException

from backend.chat import answer_instructions, answer_question
from backend.chat_planning import plan_query
from backend.config import get_settings
from backend.llm_stream import stream_text


def event(kind, data):
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def generate(session, row, user, payload, api_schema, memo):
    from backend.chat_routes import save

    question = payload.question.strip()
    steps = []
    try:
        yield event("step", {"stage": "plan", "summary": "正在判断问题是否需要补充上下文"})
        from backend.chat_context import context_status
        state = (await context_status(session, row))["state"] if get_settings().chat_context_mode == "on" else None
        plan = await plan_query(question, row.messages, state=state)
        steps.append({"stage": "plan", "action": plan.action, "query": plan.query})
        yield event("step", steps[-1])
        if plan.action == "clarify":
            result = {"answer": plan.clarification, "citations": [], "mode": "clarification"}
            yield event("delta", {"text": result["answer"]})
        else:
            yield event("step", {"stage": "act", "summary": "正在检索授权资料和查询实时状态"})
            # Keep the original wording for the scope check even after rewriting.
            query = question if plan.action == "keep" else question + "\n" + plan.query
            from backend.chat_tools import tool_context
            sources = []
            async for observation in tool_context(session, row, user, query, api_schema):
                # Each observation is re-authorized; use the latest complete set.
                sources = observation["sources"]
                step = {"stage": "tool", "round": observation["round"], "summary": f"第 {observation['round']} 轮授权检索完成"}
                steps.append(step)
                yield event("step", step)
            steps.append({"stage": "observe", "source_count": len(sources),
                "retrieval": "hybrid" if get_settings().rag_mode == "real" else "keyword"})
            yield event("step", steps[-1])
            from backend.chat_context import build_context
            context, memo, sources = await build_context(session, row, question, memo, sources)
            if get_settings().model_mode != "real" or (sources and sources[0].get("kind") == "scope"):
                result = await answer_question(question, row.messages, memo, sources, context=context)
                yield event("delta", {"text": result["answer"]})
            else:
                yield event("step", {"stage": "answer", "summary": "正在根据本轮来源生成回答"})
                answer = ""
                async for delta in stream_text(answer_instructions() +
                        " 本次直接流式输出正文，不输出 JSON 或 citation_ids 字段。引用使用 evidence 顺序编号 [1]。",
                        {"question": question, "context": context,
                         "memory": memo, "evidence": sources}):
                    answer += delta
                    yield event("delta", {"text": delta})
                numbers = {int(n) for n in re.findall(r"\[(\d+)\]", answer)}
                if any(n < 1 or n > len(sources) for n in numbers):
                    raise HTTPException(422, "回答含无效引用，本次回答未保存")
                result = {"answer": answer, "citations": [dict(s, number=i) for i, s in enumerate(sources, 1) if i in numbers], "mode": "llm"}
        message = {"request_id": str(payload.request_id), "question": question,
            "created_at": datetime.now(UTC).isoformat(), "steps": steps, **result}
        saved = await save(session, row, payload.expected_version, messages=[*row.messages, message],
            title=row.title if row.messages or row.title != "新对话" else question[:120])
        yield event("done", saved)
    except Exception as exc:
        await session.rollback()
        logging.getLogger(__name__).exception("Chat stream failed")
        yield event("error", {"message": exc.detail if isinstance(exc, HTTPException) and isinstance(exc.detail, str)
            else "模型或检索服务不可用，本次回答未保存，请重试"})
