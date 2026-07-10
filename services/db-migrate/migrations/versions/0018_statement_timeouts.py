"""role-level statement timeouts (s12 cheap hardening)

A database-side backstop against runaway queries, independent of any app-level
guard: the agent already sets a per-query timeout for run_sql, but extracts,
the SQL editor, and any future code path get nothing. Role-level
statement_timeout caps every query those roles run, no matter who wrote it.
The admin editor role gets a longer leash for heavier exploration.

Revision ID: 0018_statement_timeouts
Revises: 0017_google_identity
"""

from __future__ import annotations

from alembic import op

revision = "0018_statement_timeouts"
down_revision = "0017_google_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER ROLE app_user SET statement_timeout = '15s'")
    op.execute("ALTER ROLE agent_ro SET statement_timeout = '15s'")
    op.execute("ALTER ROLE admin_ro SET statement_timeout = '30s'")
    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0018_statement_timeouts') "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute("ALTER ROLE app_user RESET statement_timeout")
    op.execute("ALTER ROLE agent_ro RESET statement_timeout")
    op.execute("ALTER ROLE admin_ro RESET statement_timeout")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0018_statement_timeouts'")
