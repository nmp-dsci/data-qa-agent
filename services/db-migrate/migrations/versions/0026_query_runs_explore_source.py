"""query_runs source — allow 'explore' alongside 'agent' and 'sql_editor'

s19 Explore, Phase B. The /explore/aggregate endpoint audits its governed reads in
app.query_runs the same way the SQL editor does, tagged source='explore'. Widen the
source CHECK constraint to admit it. (The daily-LLM cap counts only source='agent',
so explore reads never affect caps.)

Revision ID: 0026_query_runs_explore_source
Revises: 0025_nsw_yield_dataset
"""

from __future__ import annotations

from alembic import op

revision = "0026_query_runs_explore_source"
down_revision = "0025_nsw_yield_dataset"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE app.query_runs DROP CONSTRAINT IF EXISTS query_runs_source_check")
    op.execute(
        "ALTER TABLE app.query_runs ADD CONSTRAINT query_runs_source_check "
        "CHECK (source IN ('agent', 'sql_editor', 'explore'))"
    )
    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0026_query_runs_explore_source') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    # Drop any explore rows first so the narrower constraint can be re-applied.
    op.execute("DELETE FROM app.query_runs WHERE source = 'explore'")
    op.execute("ALTER TABLE app.query_runs DROP CONSTRAINT IF EXISTS query_runs_source_check")
    op.execute(
        "ALTER TABLE app.query_runs ADD CONSTRAINT query_runs_source_check "
        "CHECK (source IN ('agent', 'sql_editor'))"
    )
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0026_query_runs_explore_source'")
