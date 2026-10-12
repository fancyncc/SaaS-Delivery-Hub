"""Immutable Run inputs/approval revisions, outbox trace context, forced core RLS."""
import sqlalchemy as sa

from alembic import op

revision = "0030_execution_safety"
down_revision = "0029_task_review_workflow"
branch_labels = None
depends_on = None

CORE_TABLES = (
    "implementation_projects", "project_documents", "agent_runs", "agent_steps",
    "approval_requests", "import_jobs", "saas_workspaces", "saas_members",
    "tool_executions", "project_artifacts", "project_feedback",
)


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "snapshot_version" not in {c["name"] for c in inspector.get_columns("agent_runs")}:
        op.add_column("agent_runs", sa.Column("snapshot_version", sa.Integer(), nullable=False, server_default="0"))
    if "trace_context" not in {c["name"] for c in inspector.get_columns("workflow_outbox")}:
        op.add_column("workflow_outbox", sa.Column("trace_context", sa.JSON(), nullable=False, server_default="{}"))
    op.create_table("run_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("customer_tenants.id"), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("implementation_projects.id"), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "sequence"))
    for column in ("tenant_id", "project_id", "run_id"):
        op.create_index(f"ix_run_snapshots_{column}", "run_snapshots", [column])
    op.create_index("uq_run_snapshot_input", "run_snapshots", ["run_id"], unique=True,
                    postgresql_where=sa.text("kind = 'input'"), sqlite_where=sa.text("kind = 'input'"))
    if op.get_bind().dialect.name == "postgresql":
        for table in CORE_TABLES:
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE run_snapshots ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE run_snapshots FORCE ROW LEVEL SECURITY")
        op.execute("""CREATE POLICY snapshot_scope ON run_snapshots
            USING (tenant_id = current_setting('app.current_tenant_id', true)
                AND EXISTS (SELECT 1 FROM agent_runs r WHERE r.id = run_snapshots.run_id
                    AND r.project_id = run_snapshots.project_id AND r.tenant_id = run_snapshots.tenant_id))
            WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)
                AND EXISTS (SELECT 1 FROM agent_runs r WHERE r.id = run_snapshots.run_id
                    AND r.project_id = run_snapshots.project_id AND r.tenant_id = run_snapshots.tenant_id))""")
        op.execute("""CREATE FUNCTION guard_execution_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_TABLE_NAME = 'run_snapshots' THEN
                    IF TG_OP = 'DELETE' AND EXISTS (SELECT 1 FROM implementation_projects p
                        WHERE p.id = OLD.project_id AND p.deleted_at <= now() - interval '30 days') THEN
                        RETURN OLD;
                    END IF;
                    RAISE EXCEPTION 'immutable run snapshot';
                ELSIF OLD.snapshot_version > 0 AND NEW.snapshot_version <> OLD.snapshot_version THEN
                    RAISE EXCEPTION 'immutable snapshot version';
                END IF;
                RETURN NEW;
            END $$""")
        op.execute("CREATE TRIGGER run_snapshots_no_update BEFORE UPDATE OR DELETE ON run_snapshots "
                   "FOR EACH ROW EXECUTE FUNCTION guard_execution_snapshot()")
        op.execute("CREATE TRIGGER agent_runs_snapshot_version BEFORE UPDATE ON agent_runs "
                   "FOR EACH ROW EXECUTE FUNCTION guard_execution_snapshot()")
    else:
        op.execute("CREATE TRIGGER IF NOT EXISTS run_snapshots_no_update BEFORE UPDATE ON run_snapshots "
                   "BEGIN SELECT RAISE(ABORT, 'immutable run snapshot'); END")
        op.execute("CREATE TRIGGER IF NOT EXISTS run_snapshots_no_delete BEFORE DELETE ON run_snapshots "
            "WHEN NOT EXISTS (SELECT 1 FROM implementation_projects p WHERE p.id = OLD.project_id "
            "AND datetime(p.deleted_at) <= datetime('now', '-30 days')) "
            "BEGIN SELECT RAISE(ABORT, 'snapshot retention requires expired project'); END")
        op.execute("CREATE TRIGGER IF NOT EXISTS agent_runs_snapshot_version BEFORE UPDATE OF snapshot_version ON agent_runs "
                   "WHEN OLD.snapshot_version > 0 AND NEW.snapshot_version <> OLD.snapshot_version "
                   "BEGIN SELECT RAISE(ABORT, 'immutable snapshot version'); END")


def downgrade():
    # Never silently discard new evidence; rollback an unused migration only.
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM run_snapshots")):
        raise RuntimeError("Preserve execution snapshots; restore a verified backup to an isolated database")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER agent_runs_snapshot_version ON agent_runs")
        op.execute("DROP TRIGGER run_snapshots_no_update ON run_snapshots")
        op.execute("DROP FUNCTION guard_execution_snapshot()")
        for table in CORE_TABLES:
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    else:
        op.execute("DROP TRIGGER IF EXISTS agent_runs_snapshot_version")
    op.drop_table("run_snapshots")
    with op.batch_alter_table("workflow_outbox") as batch:
        batch.drop_column("trace_context")
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_column("snapshot_version")
