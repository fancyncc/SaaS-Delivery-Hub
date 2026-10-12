"""Private normalized history, lossless cutover, bounded hybrid recall."""
import json
import math
import time
from collections import Counter
from uuid import NAMESPACE_URL, uuid4, uuid5

from prometheus_client import Counter as MetricCounter
from sqlalchemy import or_, select, text, update
from sqlalchemy.orm.attributes import set_committed_value

from backend.chat_models import ChatConversation, ChatHistoryMigration, ChatMessage
from backend.config import get_settings
from backend.conversation_state import digest, message_id, source_digest
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


async def hydrate(session, row, *, runtime=False, limit=None):
    marker = await session.get(ChatHistoryMigration, row.id)
    if marker and (marker.activated or (get_settings().chat_history_enabled and marker.source_digest == digest(normalize(row.messages)))):
        scoped = (ChatMessage.conversation_id == row.id, ChatMessage.tenant_id == row.tenant_id, ChatMessage.user_id == row.user_id)
        if runtime:
            # Old answers/citations are never needed to resolve the current turn.
            users = await session.execute(select(ChatMessage.turn, ChatMessage.content,
                ChatMessage.metadata_json["id"].as_string().label("message_id"),
                ChatMessage.metadata_json["request_id"].as_string().label("request_id"))
                .where(*scoped, ChatMessage.role == "user").order_by(ChatMessage.turn))
            messages = [{"id": m.message_id or m.request_id, "request_id": m.request_id,
                "question": m.content, "answer": "", "citations": []} for m in users]
            recent = decode(list(await session.scalars(select(ChatMessage).where(*scoped,
                ChatMessage.turn > max(0, marker.turn_count - 8)).order_by(ChatMessage.turn, ChatMessage.role))))
            messages[max(0, len(messages) - 8):] = recent
            row._runtime_source_digest = source_digest(messages)
        elif limit is not None:
            messages = decode(list(await session.scalars(select(ChatMessage).where(*scoped,
                ChatMessage.turn > max(0, marker.turn_count - limit)).order_by(ChatMessage.turn, ChatMessage.role))))
        else:
            messages = decode(await records(session, row))
        set_committed_value(row, "messages", messages)
        row._history_normalized = True
        row._history_light = runtime
        row._history_total = marker.turn_count
        row._history_offset = max(0, marker.turn_count - len(messages))
    return row


async def append_turn(session, row, message):
    """Append under the conversation version lock without loading old answers."""
    marker = await session.get(ChatHistoryMigration, row.id)
    if not marker or not marker.activated or marker.turn_count != row._history_total:
        raise ValueError("history_append_count_mismatch")
    turn = marker.turn_count + 1
    for role, key in (("user", "question"), ("assistant", "answer")):
        session.add(ChatMessage(id=str(uuid5(NAMESPACE_URL, f"{row.id}/{message['request_id']}/{role}")),
            tenant_id=row.tenant_id, user_id=row.user_id, conversation_id=row.id, turn=turn, role=role,
            content=message[key], metadata_json={k: v for k, v in message.items() if k not in {"question", "answer"}} if role == "user" else {}))
    marker.turn_count = turn
    # Activated histories use immutable rows; legacy digest verification runs only
    # when an explicit full-history migration/persist is requested again.
    marker.source_digest, marker.append_digest = "", ""
    if marker.index_status != "running":
        marker.index_status, marker.index_next_attempt = "pending", 0
    await session.flush()


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
    marker = await session.get(ChatHistoryMigration, row.id)
    if marker and marker.activated and marker.append_digest:
        old_count = marker.turn_count
        # Verify the prefix in memory; append never rereads every stored message.
        if len(messages) < old_count or digest(json.dumps(messages[:old_count], ensure_ascii=False, sort_keys=True)) != marker.append_digest:
            raise ValueError("history_migration_content_mismatch")
    else:
        old = decode(await records(session, row))
        if old != messages[:len(old)]:
            raise ValueError("history_migration_content_mismatch")
        old_count = len(old)
    for turn, message in enumerate(messages[old_count:], old_count + 1):
        for role, key in (("user", "question"), ("assistant", "answer")):
            session.add(ChatMessage(id=str(uuid5(NAMESPACE_URL, f"{row.id}/{message['request_id']}/{role}")),
                tenant_id=row.tenant_id, user_id=row.user_id, conversation_id=row.id, turn=turn, role=role,
                content=message[key], metadata_json={k: v for k, v in message.items() if k not in {"question", "answer"}} if role == "user" else {}))
    await session.flush()
    if not marker or not marker.activated:
        if decode(await records(session, row)) != messages:
            raise ValueError("history_migration_verification_failed")
    if not marker:
        marker = ChatHistoryMigration(conversation_id=row.id, tenant_id=row.tenant_id)
        session.add(marker)
    marker.source_digest, marker.turn_count = digest(messages), len(messages)
    marker.append_digest = digest(json.dumps(messages, ensure_ascii=False, sort_keys=True))
    marker.activated = activate or bool(marker.activated)
    if len(messages) > old_count and marker.index_status != "running":
        marker.index_status, marker.index_next_attempt = "pending", 0


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
    identifier = row.id
    identity, now, lease = embedding_identity(), time.time(), str(uuid4())
    claimed = await session.execute(update(ChatHistoryMigration).where(
        ChatHistoryMigration.conversation_id == row.id, ChatHistoryMigration.index_lease_until <= now,
        or_(ChatHistoryMigration.index_next_attempt <= now, ChatHistoryMigration.index_identity != identity),
    ).values(index_status="running", index_identity=identity, index_lease_token=lease,
        index_lease_until=now + max(300, get_settings().model_timeout * 2 + 60),
        index_attempts=ChatHistoryMigration.index_attempts + 1))
    await session.commit()
    if claimed.rowcount != 1:
        return False
    try:
        pending = list(await session.scalars(select(ChatMessage).where(ChatMessage.conversation_id == row.id,
            ChatMessage.tenant_id == row.tenant_id, ChatMessage.user_id == row.user_id,
            ChatMessage.role == "user", ChatMessage.embedding_identity != identity)
            .order_by(ChatMessage.turn).limit(limit)))
        from backend.retrieval_models import embeddings
        vectors = await embeddings([m.content for m in pending]) if pending else []
        if len(vectors) != len(pending) or any(not v or not all(math.isfinite(n) for n in v) for v in vectors):
            raise ValueError("invalid_history_embeddings")
        # Original owner must still be authorized after network inference.
        from types import SimpleNamespace

        from backend.chat_routes import owned
        from backend.worker import actor_context
        actor = await actor_context(session, SimpleNamespace(actor_id=row.user_id, tenant_id=row.tenant_id))
        await owned(session, row.id, actor, hydrate_history=False)
        fenced = await session.execute(update(ChatHistoryMigration).where(
            ChatHistoryMigration.conversation_id == row.id, ChatHistoryMigration.index_lease_token == lease,
            ChatHistoryMigration.index_lease_until > time.time(), ChatHistoryMigration.index_identity == identity,
        ).values(index_status="pending", index_next_attempt=0, index_lease_until=0, index_attempts=0))
        if fenced.rowcount != 1:
            await session.rollback()
            return False
        for message, vector in zip(pending, vectors, strict=True):
            message.embedding, message.embedding_identity = vector, identity
        await session.flush()
        remaining = await session.scalar(select(ChatMessage.id).where(ChatMessage.conversation_id == row.id,
            ChatMessage.tenant_id == row.tenant_id, ChatMessage.user_id == row.user_id,
            ChatMessage.role == "user", ChatMessage.embedding_identity != identity).limit(1))
        if not remaining:
            await session.execute(update(ChatHistoryMigration).where(ChatHistoryMigration.conversation_id == row.id,
                ChatHistoryMigration.index_lease_token == lease).values(index_status="done"))
        await session.commit()
        return True
    except Exception:
        await session.rollback()
        marker = await session.get(ChatHistoryMigration, identifier, populate_existing=True)
        if marker and marker.index_lease_token == lease:
            await session.execute(update(ChatHistoryMigration).where(ChatHistoryMigration.conversation_id == identifier,
                ChatHistoryMigration.index_lease_token == lease).values(index_status="retrying", index_lease_until=0,
                index_next_attempt=time.time() + min(300, 5 * 2 ** min(marker.index_attempts, 6))))
            await session.commit()
        RECALL.labels("index_unavailable").inc()
        return False


async def maintain_history_indexes(session, tenant, limit=20):
    """Independent durable retries also repair indexes after model changes."""
    s = get_settings()
    if not s.chat_history_enabled or s.rag_mode != "real":
        return
    now, identity = time.time(), embedding_identity()
    identifiers = list(await session.scalars(select(ChatHistoryMigration.conversation_id).where(
        ChatHistoryMigration.tenant_id == tenant, ChatHistoryMigration.index_lease_until <= now,
        or_(ChatHistoryMigration.index_identity != identity,
            (ChatHistoryMigration.index_status != "done") & (ChatHistoryMigration.index_next_attempt <= now)),
    ).order_by(ChatHistoryMigration.index_next_attempt, ChatHistoryMigration.conversation_id).limit(limit)))
    from types import SimpleNamespace

    from backend.chat_routes import owned
    from backend.worker import actor_context
    for identifier in identifiers:
        row = await session.get(ChatConversation, identifier)
        if not row:
            continue
        try:
            actor = await actor_context(session, SimpleNamespace(actor_id=row.user_id, tenant_id=tenant))
            row = await owned(session, identifier, actor, hydrate_history=False)
            await index_history(session, row)
        except Exception:
            await session.rollback()


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
            semantic = await semantic_turns(session, row, vector, identity, len(candidates), limit * 3)
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
        "adjacent_message_id": candidates[i + 1]["id"] if i + 1 < len(candidates) else "",
        "adjacent": candidates[i + 1]["question"] if i + 1 < len(candidates) else ""}
        for i in sorted(fused, key=lambda i: (-fused[i], i))[:limit]]


async def semantic_turns(session, row, vector, identity, boundary, limit):
    """Rank within the private DB scope; return only matching turn numbers."""
    norm = math.sqrt(sum(x*x for x in vector))
    if not norm or not all(math.isfinite(x) for x in vector):
        return []
    if session.bind.dialect.name == "postgresql":
        elements = ("json_array_elements_text(m.embedding) WITH ORDINALITY AS e(value, position) "
            "JOIN json_array_elements_text(CAST(:vector AS JSON)) WITH ORDINALITY AS q(value, position) "
            "ON e.position = q.position")
        numeric = "DOUBLE PRECISION"
    else:
        elements = "json_each(m.embedding) AS e JOIN json_each(:vector) AS q ON e.key = q.key"
        numeric = "REAL"
    statement = text(f"""
        SELECT turn FROM (
            SELECT m.turn, (
                SELECT SUM(CAST(e.value AS {numeric}) * CAST(q.value AS {numeric})) /
                    NULLIF(SQRT(SUM(CAST(e.value AS {numeric}) * CAST(e.value AS {numeric}))) * :norm, 0)
                FROM {elements}
            ) AS similarity FROM chat_messages m
            WHERE m.conversation_id = :conversation AND m.tenant_id = :tenant AND m.user_id = :user
                AND m.role = 'user' AND m.turn <= :boundary AND m.embedding_identity = :identity
                AND m.embedding IS NOT NULL AND json_array_length(m.embedding) = :dimensions
        ) AS ranked WHERE similarity > 0.35 ORDER BY similarity DESC, turn LIMIT :limit
    """)
    turns = await session.scalars(statement, {"vector": json.dumps(vector), "norm": norm,
        "conversation": row.id, "tenant": row.tenant_id, "user": row.user_id, "boundary": boundary,
        "identity": identity, "dimensions": len(vector), "limit": limit})
    return [turn - 1 for turn in turns]
