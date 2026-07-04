"""query_runs source column — distinguish agent runs from SQL-editor runs

SQL editor (Phase A). Editor runs are audited in the same app.query_runs table
as agent runs, but they have no natural-language question and no conversation.
This makes `question` nullable and adds a `source` column ('agent' | 'sql_editor')
so the admin dashboard can tell where a run came from.

Revision ID: 0006_query_run_source
Revises: 0005_staging_schema_grant
"""

from __future__ import annotations

from alembic import op

revision = "0006_query_run_source"
down_revision = "0005_staging_schema_grant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE app.query_runs ALTER COLUMN question DROP NOT NULL")
    op.execute(
        "ALTER TABLE app.query_runs "
        "ADD COLUMN source text NOT NULL DEFAULT 'agent' "
        "CHECK (source IN ('agent', 'sql_editor'))"
    )
    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0006_query_run_source') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE app.query_runs DROP COLUMN source")
    op.execute("UPDATE app.query_runs SET question = '' WHERE question IS NULL")
    op.execute("ALTER TABLE app.query_runs ALTER COLUMN question SET NOT NULL")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0006_query_run_source'")
