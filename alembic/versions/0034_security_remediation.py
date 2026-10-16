"""Freeze task review material and allow authorized collaborative index reads."""
import sqlalchemy as sa

from alembic import op

revision = "0034_security_remediation"
down_revision = "0033_context_maintenance"
branch_labels = None
depends_on = None


def expired_project(expression):
    return ("EXISTS (SELECT 1 FROM implementation_projects cleanup_project "
            f"WHERE cleanup_project.id = {expression} "
            "AND cleanup_project.tenant_id = current_setting('app.current_tenant_id',true) "
            "AND current_setting('app.current_company_role',true) = 'company_admin' "
            "AND cleanup_project.deleted_at <= CURRENT_TIMESTAMP - INTERVAL '30 days')")


def cleanup_policies():
    policies = {name: expired_project(f"{name}.project_id") for name in
                ("knowledge_documents", "retrieval_sources", "chat_conversations")}
    policies["retrieval_documents"] = ("EXISTS (SELECT 1 FROM knowledge_documents cleanup_doc "
        "WHERE cleanup_doc.id = retrieval_documents.origin_id AND " + expired_project("cleanup_doc.project_id") + ")")
    policies["knowledge_chunks"] = ("EXISTS (SELECT 1 FROM knowledge_documents cleanup_doc "
        "WHERE cleanup_doc.id = knowledge_chunks.document_id AND " + expired_project("cleanup_doc.project_id") + ")")
    for name in ("retrieval_nodes", "retrieval_units"):
        policies[name] = ("EXISTS (SELECT 1 FROM retrieval_documents cleanup_index JOIN knowledge_documents cleanup_doc "
            f"ON cleanup_doc.id = cleanup_index.origin_id WHERE cleanup_index.id = {name}.document_id AND "
            + expired_project("cleanup_doc.project_id") + ")")
    policies["retrieval_source_chunks"] = ("EXISTS (SELECT 1 FROM retrieval_sources cleanup_source "
        "WHERE cleanup_source.id = retrieval_source_chunks.source_id AND " + expired_project("cleanup_source.project_id") + ")")
    for name in ("chat_messages", "chat_context_snapshots", "chat_context_tasks", "chat_history_migrations", "chat_memory_candidates"):
        policies[name] = ("EXISTS (SELECT 1 FROM chat_conversations cleanup_chat "
            f"WHERE cleanup_chat.id = {name}.conversation_id AND " + expired_project("cleanup_chat.project_id") + ")")
    policies["chat_memory_items"] = ("(scope='project' AND " + expired_project("scope_id") + ") OR "
        "(scope='conversation' AND EXISTS (SELECT 1 FROM chat_conversations cleanup_chat "
        "WHERE cleanup_chat.id=chat_memory_items.scope_id AND " + expired_project("cleanup_chat.project_id") + "))")
    return policies


def upgrade():
    for name in ("retrieval_documents", "retrieval_sources"):
        columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns(name)}
        if "next_attempt" not in columns:
            op.add_column(name, sa.Column("next_attempt", sa.Float(), nullable=False, server_default="0"))
    op.create_table("cleanup_outbox",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("customer_tenants.id"), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False), sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt", sa.Float(), nullable=False), sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.String(120), nullable=False))
    op.create_index("ix_cleanup_outbox_tenant_id", "cleanup_outbox", ["tenant_id"])
    op.create_index("ix_cleanup_outbox_status", "cleanup_outbox", ["status"])
    op.add_column("project_task_reviews", sa.Column("document_snapshot", sa.JSON(), nullable=False, server_default="[]"))
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE knowledge_documents FORCE ROW LEVEL SECURITY")
        # Retention cleanup crosses collaborator tenants only for an expired project
        # owned by the current administrator's company. Ordinary reads still use
        # active-project scope checks in application code.
        for name, condition in cleanup_policies().items():
            for action in ("SELECT", "DELETE"):
                op.execute(f"CREATE POLICY expired_cleanup_{action.lower()} ON {name} FOR {action} USING ({condition})")
        op.execute("ALTER TABLE cleanup_outbox ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE cleanup_outbox FORCE ROW LEVEL SECURITY")
        op.execute("CREATE POLICY tenant_isolation ON cleanup_outbox USING (tenant_id=current_setting('app.current_tenant_id',true)) WITH CHECK (tenant_id=current_setting('app.current_tenant_id',true))")
        op.execute("CREATE POLICY knowledge_project_read ON retrieval_documents FOR SELECT USING "
                   "(EXISTS (SELECT 1 FROM knowledge_documents d WHERE d.id = retrieval_documents.origin_id "
                   "AND d.tenant_id = retrieval_documents.tenant_id AND d.project_id IS NOT NULL AND d.active))")
        op.execute("CREATE POLICY knowledge_project_read ON knowledge_chunks FOR SELECT USING "
                   "(EXISTS (SELECT 1 FROM knowledge_documents d WHERE d.id = knowledge_chunks.document_id "
                   "AND d.tenant_id = knowledge_chunks.tenant_id AND d.project_id IS NOT NULL AND d.active))")
        for name in ("retrieval_nodes", "retrieval_units"):
            op.execute(f"CREATE POLICY knowledge_project_read ON {name} FOR SELECT USING "
                       f"(EXISTS (SELECT 1 FROM retrieval_documents d WHERE d.id = {name}.document_id "
                       f"AND d.tenant_id = {name}.tenant_id))")
        op.execute("CREATE POLICY project_source_read ON retrieval_sources FOR SELECT USING "
                   "(kind IN ('knowledge','project','artifact') AND project_id IS NOT NULL AND EXISTS "
                   "(SELECT 1 FROM implementation_projects p WHERE p.id = retrieval_sources.project_id AND p.deleted_at IS NULL))")
        op.execute("CREATE POLICY project_source_read ON retrieval_source_chunks FOR SELECT USING "
                   "(EXISTS (SELECT 1 FROM retrieval_sources s WHERE s.id = retrieval_source_chunks.source_id "
                   "AND s.tenant_id = retrieval_source_chunks.tenant_id))")


def downgrade():
    for name in ("retrieval_documents", "retrieval_sources"):
        op.drop_column(name, "next_attempt")
    op.drop_table("cleanup_outbox")
    if op.get_bind().dialect.name == "postgresql":
        for name in cleanup_policies():
            for action in ("select", "delete"):
                op.execute(f"DROP POLICY expired_cleanup_{action} ON {name}")
        op.execute("ALTER TABLE knowledge_documents NO FORCE ROW LEVEL SECURITY")
        for name in ("retrieval_sources", "retrieval_source_chunks"):
            op.execute(f"DROP POLICY project_source_read ON {name}")
        for name in ("retrieval_nodes", "retrieval_units", "retrieval_documents", "knowledge_chunks"):
            op.execute(f"DROP POLICY knowledge_project_read ON {name}")
    op.drop_column("project_task_reviews", "document_snapshot")
