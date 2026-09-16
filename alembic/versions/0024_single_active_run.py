"""Enforce one active Run per project, including approval and blocked waits."""
from alembic import op
import sqlalchemy as sa

revision = "0024_single_active_run"
down_revision = "0023_rag_v3_metrics"
branch_labels = None
depends_on = None


def upgrade():
    # Existing duplicates intentionally fail migration: do not silently cancel
    # a Run whose external effects may already have occurred.
    predicate = sa.text("status IN ('pending','running','preparing_materials','waiting_approval','blocked')")
    op.create_index("uq_agent_run_active_project", "agent_runs", ["project_id"],
                    unique=True, postgresql_where=predicate, sqlite_where=predicate)


def downgrade():
    op.drop_index("uq_agent_run_active_project", table_name="agent_runs")
