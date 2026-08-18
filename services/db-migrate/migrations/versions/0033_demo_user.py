"""demo user — the seeded walk-in identity for demo-mode deployments (s38 P0)

Every visitor to a demo deployment shares this one app.users row: /auth/demo-login
mints its session, RLS scopes every query to it, and the nightly reset clears its
accumulated conversations. Granted read on every dataset so the demo shows the
product working, not the isolation empty-state (user2 already demonstrates that
in the recorded runs). Plan 'pro' so replayed answers carry the full page set.

Idempotent by username, like the 03_seed users — safe on a database that already
has it.

Revision ID: 0033_demo_user
Revises: 0032_service_accounts
"""

from __future__ import annotations

from alembic import op

revision = "0033_demo_user"
down_revision = "0032_service_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO app.users (username, email, display_name, role, plan) "
        "VALUES ('demo', 'demo@example.com', 'Demo Visitor', 'user', 'pro') "
        "ON CONFLICT (username) DO NOTHING"
    )
    op.execute(
        "INSERT INTO app.dataset_access (dataset_id, user_id, access) "
        "SELECT d.id, u.id, 'read' FROM app.datasets d, app.users u "
        "WHERE u.username = 'demo' "
        "ON CONFLICT DO NOTHING"
    )
    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0033_demo_user') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM app.dataset_access WHERE user_id = "
        "(SELECT id FROM app.users WHERE username = 'demo')"
    )
    op.execute("DELETE FROM app.users WHERE username = 'demo'")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0033_demo_user'")
