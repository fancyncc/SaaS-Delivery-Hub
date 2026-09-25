"""Private normalized history, lossless cutover, bounded hybrid recall."""
import math
from collections import Counter
from uuid import NAMESPACE_URL, uuid5

from prometheus_client import Counter as MetricCounter
from sqlalchemy import select
from sqlalchemy.orm.attributes import set_committed_value

from backend.chat_models import ChatHistoryMigration, ChatMessage
from backend.config import get_settings
from backend.conversation_state import digest, message_id
from backend.knowledge import lexemes

RECALL = MetricCounter("saas_history_recall_total", "Private history recall", ["mode"])


def normalize(messages):
    return [dict(m, id=message_id(m, i)) for i, m in enumerate(messages)]


async def records(session, row):
    return list(await session.scalars(select(ChatMessage).where(ChatMessage.conversation_id == row.id,
        ChatMessage.tenant_id == row.tenant_id, ChatMessage.user_id == row.user_id)
        .order_by(ChatMessage.turn, ChatMessage.role)))


def decode(rows):
    turns = {}
    for message in rows:
        value = turns.setdefault(message.turn, {})
        if message.role == "user":
            value.update(message.metadata_json)
            value["question"] = message.content
        else:
            value["answer"] = message.content
    return [turns[key] for key in sorted(turns)]


async def hydrate(session, row):
    marker = await session.get(ChatHistoryMigration, row.id)
    if marker and (marker.activated or (get_settings().chat_history_enabled and marker.source_digest == digest(normalize(row.messages)))):
        set_committed_value(row, "messages", decode(await records(session, row)))
        row._history_normalized = True
    return row


async def page(session, row, before=None, limit=50):
    marker = await session.get(ChatHistoryMigration, row.id)
    if not marker or not (marker.activated or (get_settings().chat_history_enabled and marker.source_digest == digest(normalize(row.messages)))):
        end = min(len(row.messages), before - 1) if before else len(row.messages)
        start = max(0, end - limit)
        return {"messages": row.messages[start:end], "next_before": start + 1 if start else None, "message_count": len(row.messages)}
    scoped = (ChatMessage.conversation_id == row.id, ChatMessage.tenant_id == row.tenant_id, ChatMessage.user_id == row.user_id)
    query = select(ChatMessage.turn).where(*scoped, ChatMessage.role == "user")
    if before:
        query = query.where(ChatMessage.turn < before)
    turns = list(await session.scalars(query.order_by(ChatMessage.turn.desc()).limit(limit)))
    rows = list(await session.scalars(select(ChatMessage).where(*scoped, ChatMessage.turn.in_(turns)))) if turns else []
    return {"messages": decode(rows), "next_before": min(turns) if turns and min(turns) > 1 else None,
        "message_count": marker.turn_count}


async def persist(session, row, messages, *, activate=True):
    messages = normalize(messages)
    existing = await records(session, row)
    old = decode(existing)
    if old != messages[:len(old)]:
        raise ValueError("history_migration_content_mismatch")
    for turn, message in enumerate(messages[len(old):], len(old) + 1):
        for role, key in (("user", "question"), ("assistant", "answer")):
            session.add(ChatMessage(id=str(uuid5(NAMESPACE_URL, f"{row.id}/{message['request_id']}/{role}")),
                tenant_id=row.tenant_id, user_id=row.user_id, conversation_id=row.id, turn=turn, role=role,
                content=message[key], metadata_json={k: v for k, v in message.items() if k not in {"question", "answer"}} if role == "user" else {}))
    await session.flush()
    if decode(await records(session, row)) != messages:
        raise ValueError("history_migration_verification_failed")
    marker = await session.get(ChatHistoryMigration, row.id)
    if not marker:
        marker = ChatHistoryMigration(conversation_id=row.id, tenant_id=row.tenant_id)
        session.add(marker)
    marker.source_digest, marker.turn_count = digest(messages), len(messages)
    marker.activated = activate or bool(marker.activated)


def embedding_identity():
    s = get_settings()
    return digest([s.embedding_mode, s.embedding_local_model, s.embedding_model, s.embedding_revision, s.embedding_dimensions])


async def index_history(session, row, limit=8):
    if not get_settings().chat_history_enabled:
        return
    marker = await session.get(ChatHistoryMigration, row.id)
    if not marker:
        return
    if get_settings().rag_mode != "real":
        return
    identity = embedding_identity()
    pending = [m for m in await records(session, row) if m.role == "user" and m.embedding_identity != identity][:limit]
    if not pending:
        return
    from backend.retrieval_models import embeddings
    try:
        vectors = await embeddings([m.content for m in pending])
        if len(vectors) != len(pending):
            raise ValueError("embedding_count")
        for message, vector in zip(pending, vectors, strict=True):
            message.embedding, message.embedding_identity = vector, identity
    except Exception:
        RECALL.labels("index_unavailable").inc()


async def recall_history(session, row, query, limit=6):
    if not get_settings().chat_history_enabled:
        return []
    history = normalize(row.messages)
    if len(history) <= 8:
        return []
    candidates = history[:-8]
    terms = set(lexemes(query).split())
    tokens = [Counter(lexemes(m["question"]).split()) for m in candidates]
    avg = sum(sum(t.values()) for t in tokens) / max(1, len(tokens))
    df = Counter(t for counts in tokens for t in counts)
    scores = []
    for i, counts in enumerate(tokens):
        score = sum(math.log(1 + (len(tokens) - df[t] + 0.5) / (df[t] + 0.5)) *
            counts[t] * 2.2 / (counts[t] + 1.2 * (0.25 + 0.75 * sum(counts.values()) / max(1, avg)))
            for t in terms if counts[t])
        if score:
            scores.append((i, score))
    lexical = [i for i, _ in sorted(scores, key=lambda x: (-x[1], x[0]))]
    semantic = []
    if get_settings().rag_mode == "real":
        from backend.retrieval_models import embeddings
        try:
            vector = (await embeddings([query], query=True))[0]
            identity = embedding_identity()
            scored = []
            for message in await records(session, row):
                if message.role != "user" or message.turn > len(candidates) or message.embedding_identity != identity or not message.embedding:
                    continue
                if len(vector) != len(message.embedding):
                    continue
                denominator = math.sqrt(sum(x*x for x in vector) * sum(x*x for x in message.embedding))
                score = sum(a*b for a, b in zip(vector, message.embedding, strict=True)) / denominator if denominator else 0
                if score > 0.35:
                    scored.append((message.turn - 1, score))
            semantic = [i for i, _ in sorted(scored, key=lambda x: (-x[1], x[0]))]
            RECALL.labels("hybrid").inc()
        except Exception:
            RECALL.labels("lexical_fallback").inc()
    else:
        RECALL.labels("lexical").inc()
    fused = Counter()
    for ranking in (lexical, semantic):
        for rank, i in enumerate(ranking):
            fused[i] += 1 / (60 + rank + 1)
    return [{"kind": "conversation_history", "message_id": candidates[i]["id"], "turn": i + 1,
        "text": candidates[i]["question"], "source": "当前对话用户原文",
        "adjacent": candidates[i + 1]["question"] if i + 1 < len(candidates) else ""}
        for i in sorted(fused, key=lambda i: (-fused[i], i))[:limit]]
