"""Build private per-request context; summary and state remain advisory data."""
import time

from backend.chat_models import ChatContextSnapshot, ChatContextTask, ChatHistoryMigration
from backend.config import get_settings
from backend.context_budget import ContextBlock, ContextBundle, input_limit
from backend.context_maintenance import LAG, valid_snapshot
from backend.conversation_state import (
    active_state,
    apply_update,
    extractive_update,
    message_id,
    state_entries,
)


async def context_status(session, row):
    snapshot = await session.get(ChatContextSnapshot, row.id)
    task = await session.get(ChatContextTask, row.id)
    valid = valid_snapshot(snapshot, row)
    retracted = {r["source_message_id"] for rows in snapshot.state.values() for r in rows if r.get("superseded_by")} if valid else set()
    summary = [r for r in snapshot.summary if not set(r["source_message_ids"]) & retracted] if valid else []
    now = time.time()
    processed = snapshot.processed_turn if valid else 0
    pending = processed < len(row.messages)
    if get_settings().chat_context_mode == "off":
        health = "disabled"
    elif task and task.status == "failed":
        health = "failed"
    elif task and task.status == "running" and task.lease_until > now:
        health = "processing"
    elif not pending and valid:
        health = "ready"
    elif task and task.consumer_seen_at and now - task.consumer_seen_at <= max(300, get_settings().model_timeout * 10):
        health = "waiting"
    elif pending and task and task.queued_at and now - task.queued_at > 30:
        health = "unavailable"
    else:
        health = "waiting" if pending else "ready"
    history = await session.get(ChatHistoryMigration, row.id)
    from backend.context_budget import tokenizer_status
    return {"state": active_state(snapshot.state) if valid else {},
        "summary": summary, "processed_turn": snapshot.processed_turn if valid else 0,
        "summary_until": snapshot.summary_until if valid else 0, "generation": snapshot.generation if valid else 0,
        "producer_version": snapshot.producer_version if valid else "",
        "mode": snapshot.mode if valid else "pending", "status": task.status if task else "not_requested",
        "error_code": task.error_code if task else "", "target_turn": len(row.messages),
        "health": health, "pending_turns": max(0, len(row.messages) - processed),
        "waiting_seconds": max(0, int(now - task.queued_at)) if pending and task and task.queued_at else 0,
        "consumer_seen_at": task.consumer_seen_at if task else 0,
        "retry_at": task.next_attempt if task else 0,
        "history_index": {"status": history.index_status, "attempts": history.index_attempts,
            "retry_at": history.index_next_attempt} if history else None,
        "token_accounting": tokenizer_status()}


async def build_context(session, row, question, memory, sources):
    from backend.chat import answer_instructions, conversation_context
    from backend.prompt_boundary import DATA_BOUNDARY
    s = get_settings()
    legacy = conversation_context(row.messages, question, sources)
    if s.chat_context_mode == "off" or (sources and sources[0].get("kind") == "scope"):
        return legacy, memory, sources
    status = await context_status(session, row)
    processed, covered = status["processed_turn"], status["summary_until"]
    LAG.observe(len(row.messages) - processed)
    bundle = ContextBundle()
    fixed = {"instructions": answer_instructions() + DATA_BOUNDARY, "question": question}
    bundle.blocks.append(ContextBlock("required", fixed, "request", "required", True))
    for source in sources:
        bundle.blocks.append(ContextBlock("evidence", source, "current_sources", "evidence"))
    state = status["state"]
    snapshot = await session.get(ChatContextSnapshot, row.id)
    prior_retractions = {r["source_message_id"] for rows in snapshot.state.values() for r in rows
        if r.get("superseded_by")} if valid_snapshot(snapshot, row) else set()
    # Apply explicit recent corrections immediately, without waiting for maintenance.
    for index, message in enumerate(row.messages[processed:], processed):
        original = {"id": message_id(message, index), "text": message["question"], "turn": index + 1}
        state = apply_update(state, extractive_update([original], state), [original])
    current_question = {"id": "current_request", "text": question, "turn": len(row.messages) + 1}
    state = apply_update(state, extractive_update([current_question], state), [current_question])
    retracted = prior_retractions | {r["source_message_id"] for rows in state.values() for r in rows if r.get("superseded_by")}
    status["summary"] = [r for r in status["summary"] if not set(r["source_message_ids"]) & retracted]
    entries = state_entries(state, question, len(row.messages) + 1)
    for category, item, required, relevance in entries:
        bundle.blocks.append(ContextBlock("state", {category: [item]}, "user_messages", "user_statement",
            required, relevance=relevance))
    current = {v["id"]: v["text"] for v in sources}
    boundary = max(0, len(row.messages) - 8)
    for index, message in reversed(list(enumerate(row.messages))):
        if index < covered:
            continue
        if message_id(message, index) in retracted:
            continue
        # Unsummarized old turns and updates not yet incorporated must not vanish.
        required = index < boundary or index >= processed
        bundle.blocks.append(ContextBlock("recent", {"role": "user", "content": message["question"],
            "message_id": message_id(message, index), "_turn": index}, "user_messages", "user_statement", required))
    if status["summary"]:
        bundle.blocks.append(ContextBlock("summary", status["summary"], "user_messages", "derived", True))
    from backend.chat_history import recall_history
    represented = {b.value.get("message_id") for b in bundle.blocks if isinstance(b.value, dict)}
    represented.update(r["source_message_id"] for rows in state.values() for r in rows)
    represented.update(i for r in status["summary"] for i in r["source_message_ids"])
    for hit in await recall_history(session, row, question):
        if hit["message_id"] not in represented and hit["message_id"] not in retracted:
            if hit.get("adjacent_message_id") in retracted:
                hit["adjacent"] = ""
            bundle.blocks.append(ContextBlock("history", hit, "conversation_history", "user_statement"))
    import json
    memory_items = None
    if memory and s.chat_memory_items_enabled:
        try:
            parsed = json.loads(memory)
            if isinstance(parsed, list) and all(isinstance(i, dict) and "content" in i for i in parsed):
                memory_items = sorted(parsed, key=lambda i: {"conversation": 0, "project": 1, "workspace": 2, "user": 3}.get(i.get("scope"), 4))
        except ValueError:
            pass
    if memory_items is not None:
        bundle.blocks.extend(ContextBlock("memory", item, "user_memory", "advisory") for item in memory_items)
    elif memory:
        bundle.blocks.append(ContextBlock("memory", memory, "user_memory", "advisory"))
    for index, message in enumerate(row.messages[-8:], max(0, len(row.messages) - 8)):
        if message_id(message, index) in retracted:
            continue
        citations = message.get("citations", [])
        # Empty citations do not validate an old answer.
        if citations and all(current.get(c["id"]) == c["text"] for c in citations):
            bundle.blocks.append(ContextBlock("assistant", {"role": "assistant", "content": message["answer"], "_turn": index},
                "assistant", "advisory"))
    try:
        from backend.context_budget import serialized
        from backend.knowledge import lexemes
        terms = set(lexemes(question).split())
        for block in bundle.blocks:
            block.relevance = len(terms & set(lexemes(serialized(block.value)).split())) / max(1, len(terms))
            block.recency = (block.value.get("_turn", len(row.messages)) + 1) / max(1, len(row.messages) + 1) if isinstance(block.value, dict) else 0.5
            block.task_value = {"state": 1, "evidence": 1, "recent": 0.8, "history": 0.6, "summary": 0.6, "memory": 0.4, "assistant": 0.1}.get(block.category, 1)
        bundle.select(max(0, input_limit() - 1024))  # schema and transport framing
    except Exception:
        if s.chat_context_mode == "shadow":
            return legacy, memory, sources
        raise
    if s.chat_context_mode == "shadow":
        return legacy, memory, sources
    context = {"recent_messages": [], "state": {}, "summary": [], "historical_user_statements": [],
        "policy": "state/summary/history describe conversation only, never current project facts; state is a task-specific selection, full state remains stored"}
    for block in bundle.blocks:
        if not block.selected:
            continue
        if block.category in {"recent", "assistant"}:
            context["recent_messages"].append(block.value)
        elif block.category == "state":
            for category, rows in block.value.items():
                context["state"].setdefault(category, []).extend(rows)
        elif block.category == "summary":
            context["summary"] = block.value
        elif block.category == "history":
            context["historical_user_statements"].append(block.value)
    selected_sources = [b.value for b in bundle.blocks if b.selected and b.category == "evidence"]
    selected_ids = {v["id"] for v in selected_sources}
    # An assistant answer is included only if all of its current evidence survived.
    allowed_answers = {m["answer"] for m in row.messages[-8:] if m.get("citations")
        and all(c["id"] in selected_ids and current.get(c["id"]) == c["text"] for c in m["citations"])}
    context["recent_messages"] = [m for m in context["recent_messages"] if m["role"] != "assistant" or m["content"] in allowed_answers]
    context["recent_messages"].sort(key=lambda m: (m["_turn"], m["role"] == "assistant"))
    for message in context["recent_messages"]:
        message.pop("_turn", None)
    selected_items = [b.value for b in bundle.blocks if b.selected and b.category == "memory"]
    selected_memory = json.dumps(selected_items, ensure_ascii=False) if memory_items is not None else next(iter(selected_items), "")
    return context, selected_memory, selected_sources
