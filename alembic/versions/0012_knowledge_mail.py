"""Versioned tenant knowledge and queued account mail.

Revision ID: 0012_knowledge_mail
Revises: 0011_workflow_outbox
"""
import sqlalchemy as sa

from alembic import op

revision = "0012_knowledge_mail"
down_revision = "0011_workflow_outbox"
branch_labels = None
depends_on = None


def upgrade():
    names = sa.inspect(op.get_bind()).get_table_names()
    if "knowledge_documents" not in names:
        op.create_table("knowledge_documents", sa.Column("id", sa.String(36), primary_key=True), sa.Column("tenant_id", sa.String(36), sa.ForeignKey("customer_tenants.id"), nullable=False), sa.Column("title", sa.String(160), nullable=False), sa.Column("version", sa.Integer(), nullable=False), sa.Column("module", sa.String(60), nullable=False), sa.Column("source", sa.String(500), nullable=False), sa.Column("license", sa.String(500), nullable=False), sa.Column("body", sa.Text(), nullable=False), sa.Column("active", sa.Boolean(), nullable=False), sa.Column("embedding", sa.JSON(), nullable=True), sa.Column("embedding_model", sa.String(120), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("tenant_id", "title", "version"))
        op.create_index("ix_knowledge_documents_tenant_id", "knowledge_documents", ["tenant_id"])
    if "mail_deliveries" not in names:
        op.create_table("mail_deliveries", sa.Column("id", sa.String(36), primary_key=True), sa.Column("email", sa.String(160), nullable=False), sa.Column("purpose", sa.String(80), nullable=False), sa.Column("body", sa.Text(), nullable=False), sa.Column("status", sa.String(20), nullable=False), sa.Column("attempts", sa.Integer(), nullable=False), sa.Column("last_error", sa.String(120), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
        op.create_index("ix_mail_deliveries_status", "mail_deliveries", ["status"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute("ALTER TABLE knowledge_documents ENABLE ROW LEVEL SECURITY")
        op.execute("CREATE POLICY knowledge_tenant ON knowledge_documents USING (tenant_id = current_setting('app.current_tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))")


def downgrade():
    op.drop_table("mail_deliveries")
    op.drop_table("knowledge_documents")
