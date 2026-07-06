"""query_runs trace column — persist the agent's step-by-step run trace

Admins can inspect how the agent reached an answer (each SQL attempt, chart, and
memory write). The trace is stored per run as JSON so the admin dashboard can
show it for every agent run and the chat UI can expand it for admins. Nullable —
SQL-editor runs and older rows simply have no trace.

Revision ID: 0007_query_run_trace
Revises: 0006_query_run_source
"""

from __future__ import annotations

from alembic import op

revision = "0007_query_run_trace"
down_revision = "0006_query_run_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE app.query_runs ADD COLUMN trace jsonb")
    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0007_query_run_trace') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE app.query_runs DROP COLUMN trace")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0007_query_run_trace'")
