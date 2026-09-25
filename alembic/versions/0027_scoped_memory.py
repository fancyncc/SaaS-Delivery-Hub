"""Private normalized history, scoped memory, candidates and verified lessons."""
import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa

from alembic import op

revision = "0027_scoped_memory"
down_revision = "0026_chat_context"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table('chat_messages'):
        op.create_table('chat_messages',
            sa.Column('id', sa.String(length=36), nullable=False, primary_key=True),
            sa.Column('tenant_id', sa.String(length=36), sa.ForeignKey('customer_tenants.id'), nullable=False),
            sa.Column('user_id', sa.String(length=36), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('conversation_id', sa.String(length=36), sa.ForeignKey('chat_conversations.id', ondelete='CASCADE'), nullable=False),
            sa.Column('turn', sa.Integer(), nullable=False),
            sa.Column('role', sa.String(length=16), nullable=False),
            sa.Column('content', sa.Text(), nullable=False),
            sa.Column('metadata_json', sa.JSON(), nullable=False),
            sa.Column('embedding', sa.JSON(), nullable=True),
            sa.Column('embedding_identity', sa.String(length=200), nullable=False),
            sa.UniqueConstraint('conversation_id', 'turn', 'role'),
        )
        op.create_index('ix_chat_messages_tenant_id', 'chat_messages', ['tenant_id'])
        op.create_index('ix_chat_messages_user_id', 'chat_messages', ['user_id'])
        op.create_index('ix_chat_messages_conversation_id', 'chat_messages', ['conversation_id'])
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE chat_messages ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE chat_messages FORCE ROW LEVEL SECURITY")
        op.execute("CREATE POLICY tenant_isolation ON chat_messages USING (tenant_id = current_setting('app.current_tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))")
    if not sa.inspect(bind).has_table('chat_history_migrations'):
        op.create_table('chat_history_migrations',
            sa.Column('conversation_id', sa.String(length=36), sa.ForeignKey('chat_conversations.id', ondelete='CASCADE'), nullable=False, primary_key=True),
            sa.Column('tenant_id', sa.String(length=36), sa.ForeignKey('customer_tenants.id'), nullable=False),
            sa.Column('source_digest', sa.String(length=64), nullable=False),
            sa.Column('turn_count', sa.Integer(), nullable=False),
            sa.Column('activated', sa.Boolean(), nullable=False),
        )
        op.create_index('ix_chat_history_migrations_tenant_id', 'chat_history_migrations', ['tenant_id'])
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE chat_history_migrations ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE chat_history_migrations FORCE ROW LEVEL SECURITY")
        op.execute("CREATE POLICY tenant_isolation ON chat_history_migrations USING (tenant_id = current_setting('app.current_tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))")
    if not sa.inspect(bind).has_table('chat_memory_items'):
        op.create_table('chat_memory_items',
            sa.Column('id', sa.String(length=36), nullable=False, primary_key=True),
            sa.Column('user_id', sa.String(length=36), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('tenant_id', sa.String(length=36), sa.ForeignKey('customer_tenants.id'), nullable=True),
            sa.Column('scope', sa.String(length=20), nullable=False),
            sa.Column('scope_id', sa.String(length=36), nullable=False),
            sa.Column('category', sa.String(length=24), nullable=False),
            sa.Column('key', sa.String(length=100), nullable=False),
            sa.Column('content', sa.Text(), nullable=False),
            sa.Column('source', sa.JSON(), nullable=False),
            sa.Column('status', sa.String(length=20), nullable=False),
            sa.Column('version', sa.Integer(), nullable=False),
            sa.Column('expires_at', sa.Float(), nullable=True),
            sa.UniqueConstraint('user_id', 'scope', 'scope_id', 'key'),
        )
        op.create_index('ix_chat_memory_items_tenant_id', 'chat_memory_items', ['tenant_id'])
        op.create_index('ix_chat_memory_items_user_id', 'chat_memory_items', ['user_id'])
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE chat_memory_items ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE chat_memory_items FORCE ROW LEVEL SECURITY")
        op.execute("CREATE POLICY tenant_isolation ON chat_memory_items USING (user_id = current_setting('app.current_user_id', true) AND (tenant_id = current_setting('app.current_tenant_id', true) OR (scope = 'user' AND tenant_id IS NULL))) WITH CHECK (user_id = current_setting('app.current_user_id', true) AND (tenant_id = current_setting('app.current_tenant_id', true) OR (scope = 'user' AND tenant_id IS NULL)))")
    if not sa.inspect(bind).has_table('chat_memory_preferences'):
        op.create_table('chat_memory_preferences',
            sa.Column('id', sa.String(length=36), nullable=False, primary_key=True),
            sa.Column('tenant_id', sa.String(length=36), sa.ForeignKey('customer_tenants.id'), nullable=False),
            sa.Column('user_id', sa.String(length=36), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('auto_extract', sa.Boolean(), nullable=False),
            sa.Column('version', sa.Integer(), nullable=False),
            sa.UniqueConstraint('tenant_id', 'user_id'),
        )
        op.create_index('ix_chat_memory_preferences_user_id', 'chat_memory_preferences', ['user_id'])
        op.create_index('ix_chat_memory_preferences_tenant_id', 'chat_memory_preferences', ['tenant_id'])
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE chat_memory_preferences ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE chat_memory_preferences FORCE ROW LEVEL SECURITY")
        op.execute("CREATE POLICY tenant_isolation ON chat_memory_preferences USING (tenant_id = current_setting('app.current_tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))")
    if not sa.inspect(bind).has_table('chat_memory_candidates'):
        op.create_table('chat_memory_candidates',
            sa.Column('id', sa.String(length=36), nullable=False, primary_key=True),
            sa.Column('tenant_id', sa.String(length=36), sa.ForeignKey('customer_tenants.id'), nullable=False),
            sa.Column('user_id', sa.String(length=36), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('conversation_id', sa.String(length=36), sa.ForeignKey('chat_conversations.id', ondelete='CASCADE'), nullable=False),
            sa.Column('source_message_id', sa.String(length=36), nullable=False),
            sa.Column('fingerprint', sa.String(length=64), nullable=False),
            sa.Column('content', sa.Text(), nullable=False),
            sa.Column('category', sa.String(length=24), nullable=False),
            sa.Column('key', sa.String(length=100), nullable=False),
            sa.Column('status', sa.String(length=20), nullable=False),
            sa.Column('version', sa.Integer(), nullable=False),
            sa.Column('expires_at', sa.Float(), nullable=False),
            sa.Column('memory_item_id', sa.String(length=36), nullable=True),
            sa.UniqueConstraint('tenant_id', 'user_id', 'fingerprint'),
        )
        op.create_index('ix_chat_memory_candidates_conversation_id', 'chat_memory_candidates', ['conversation_id'])
        op.create_index('ix_chat_memory_candidates_tenant_id', 'chat_memory_candidates', ['tenant_id'])
        op.create_index('ix_chat_memory_candidates_user_id', 'chat_memory_candidates', ['user_id'])
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE chat_memory_candidates ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE chat_memory_candidates FORCE ROW LEVEL SECURITY")
        op.execute("CREATE POLICY tenant_isolation ON chat_memory_candidates USING (tenant_id = current_setting('app.current_tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))")
    if not sa.inspect(bind).has_table('agent_lessons'):
        op.create_table('agent_lessons',
            sa.Column('id', sa.String(length=36), nullable=False, primary_key=True),
            sa.Column('tenant_id', sa.String(length=36), sa.ForeignKey('customer_tenants.id'), nullable=False),
            sa.Column('project_id', sa.String(length=36), sa.ForeignKey('implementation_projects.id'), nullable=False),
            sa.Column('run_id', sa.String(length=36), sa.ForeignKey('agent_runs.id'), nullable=False),
            sa.Column('tool', sa.String(length=80), nullable=False),
            sa.Column('category', sa.String(length=32), nullable=False),
            sa.Column('workflow_version', sa.String(length=40), nullable=False),
            sa.Column('advice', sa.Text(), nullable=False),
            sa.Column('evidence', sa.JSON(), nullable=False),
            sa.Column('verified', sa.Boolean(), nullable=False),
            sa.UniqueConstraint('project_id', 'tool', 'category', 'workflow_version'),
        )
        op.create_index('ix_agent_lessons_tenant_id', 'agent_lessons', ['tenant_id'])
        op.create_index('ix_agent_lessons_project_id', 'agent_lessons', ['project_id'])
        op.create_index('ix_agent_lessons_run_id', 'agent_lessons', ['run_id'])
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE agent_lessons ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE agent_lessons FORCE ROW LEVEL SECURITY")
        op.execute("CREATE POLICY tenant_isolation ON agent_lessons USING (tenant_id = current_setting('app.current_tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))")
    backfill(bind)


def downgrade():
    op.drop_table('agent_lessons')
    op.drop_table('chat_memory_candidates')
    op.drop_table('chat_memory_preferences')
    op.drop_table('chat_memory_items')
    op.drop_table('chat_history_migrations')
    op.drop_table('chat_messages')


def backfill(bind):
    # Frozen data conversion: no model calls, no changed application helpers.
    metadata = sa.MetaData()
    metadata.reflect(bind, only=["chat_conversations", "chat_messages", "chat_history_migrations", "chat_memories", "chat_memory_items"], resolve_fks=False)
    conversations, messages, markers = (metadata.tables[n] for n in ("chat_conversations", "chat_messages", "chat_history_migrations"))
    memories, items = (metadata.tables[n] for n in ("chat_memories", "chat_memory_items"))
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("SET LOCAL row_security = off"))
    for row in bind.execute(sa.select(conversations)).mappings():
        if bind.execute(sa.select(markers.c.conversation_id).where(markers.c.conversation_id == row["id"])).first():
            continue
        turns = []
        for ordinal, original in enumerate(row["messages"]):
            value = dict(original)
            value["id"] = value.get("id") or value["request_id"]
            turns.append(value)
            for role, key in (("user", "question"), ("assistant", "answer")):
                bind.execute(messages.insert().values(id=str(uuid5(NAMESPACE_URL, f"{row['id']}/{value['request_id']}/{role}")),
                    tenant_id=row["tenant_id"], user_id=row["user_id"], conversation_id=row["id"], turn=ordinal + 1,
                    role=role, content=value[key], metadata_json={k: v for k, v in value.items() if k not in {"question", "answer"}} if role == "user" else {},
                    embedding=None, embedding_identity=""))
        restored = {}
        for message in bind.execute(sa.select(messages).where(messages.c.conversation_id == row["id"])).mappings():
            value = restored.setdefault(message["turn"], {})
            if message["role"] == "user":
                value.update(message["metadata_json"])
                value["question"] = message["content"]
            else:
                value["answer"] = message["content"]
        if [restored[k] for k in sorted(restored)] != turns:
            raise ValueError("history_migration_verification_failed")
        fingerprint = hashlib.sha256(json.dumps(turns, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        bind.execute(markers.insert().values(conversation_id=row["id"], tenant_id=row["tenant_id"],
            source_digest=fingerprint, turn_count=len(turns), activated=False))
    for memory in bind.execute(sa.select(memories)).mappings():
        if bind.execute(sa.select(items.c.id).where(items.c.user_id == memory["user_id"],
            items.c.scope == "workspace", items.c.scope_id == memory["tenant_id"], items.c.key == "__legacy_workspace__")).first():
            continue
        bind.execute(items.insert().values(id=str(uuid5(NAMESPACE_URL, "legacy-memory/" + memory["id"])),
            user_id=memory["user_id"], tenant_id=memory["tenant_id"], scope="workspace", scope_id=memory["tenant_id"],
            category="legacy", key="__legacy_workspace__", content=memory["content"], source={"kind": "legacy_import"},
            status="active" if memory["content"] else "revoked", version=memory["version"], expires_at=None))
