"""query_runs channel column — capture the entry point every run came through

`source` (0006) records *what kind* of run it is ('agent' | 'sql_editor').
This adds `channel`: *where* the run entered from — the client/platform that
issued it. The web app sends an `X-Client-Channel: web` header; a run with no
such header (a direct hit on the backend API) is recorded as 'api'. New channels
(slack, mobile, ...) just send their own value — no schema change needed, so the
column is deliberately free-text (no CHECK constraint) to stay future-proof.

Backfill: existing rows predate the header, so 'api' is the honest default for
them too (they were not attributed to the web client).

Revision ID: 0011_query_run_channel
Revises: 0010_query_runs_editor_grant
"""

from __future__ import annotations

from alembic import op

revision = "0011_query_run_channel"
down_revision = "0010_query_runs_editor_grant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE app.query_runs ADD COLUMN channel text NOT NULL DEFAULT 'api'")
    op.execute("CREATE INDEX ON app.query_runs (channel)")
    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0011_query_run_channel') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE app.query_runs DROP COLUMN channel")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0011_query_run_channel'")
