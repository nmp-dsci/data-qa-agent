"""query_runs editor grant — agent_ro can SELECT app.query_runs (RLS-scoped)

Admins asked to inspect the run log from the SQL editor. The editor executes as
the read-only `agent_ro` role, which had no SELECT privilege on app.query_runs,
so a query returned "permission denied for table query_runs" — even though the
table already carries the RLS policy `is_admin() OR user_id = current_user_id()`
(db/init/02_rls.sql). The missing piece was purely the table-level grant.

Granting SELECT is safe: `agent_ro` is NOSUPERUSER/NOBYPASSRLS, so the existing
RLS policy scopes visibility exactly as intended — an admin sees every run, any
other user sees only their own. Read-only; no INSERT/UPDATE/DELETE. Only
query_runs is opened (not messages/conversations/user_memories), keeping the
editor's operational-table surface minimal.

Revision ID: 0010_query_runs_editor_grant
Revises: 0009_feedback_context
"""

from __future__ import annotations

from alembic import op

revision = "0010_query_runs_editor_grant"
down_revision = "0009_feedback_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT SELECT ON app.query_runs TO agent_ro")
    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0010_query_runs_editor_grant') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("REVOKE SELECT ON app.query_runs FROM agent_ro")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0010_query_runs_editor_grant'")
