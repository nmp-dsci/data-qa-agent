"""service accounts — a non-human caller that is still a real user (s35 rung 0)

Everything downstream of ``get_current_user`` already works for a machine: /ask
is synchronous, RLS scopes on ``app.users.id``, and ``query_runs.channel`` was
deliberately left free-text (0011) so new surfaces need no schema change. The
only missing piece was a way for a non-human to *become* an ``app.users`` row.

A service account IS a user (``auth_provider='service'``), so grants, RLS,
conversations and the audit trail all work unchanged — no parallel identity
system. The key's secret half is never stored; only its SHA-256. ``surface``
pins a key to one front door, so a leaked Slack key cannot drive the MCP server.

Deliberately NOT here: any act-as / delegation schema. v1 is a bot identity with
its own grants, where channel membership is the access boundary (s35). Shipping
columns nobody reads is its own smell — delegation gets its own migration on the
day it exists.

Note ``app.service_accounts`` is granted to app_user only, never to agent_ro:
the agent runs model-authored SQL and must not be able to select key hashes.

Revision ID: 0032_service_accounts
Revises: 0031_ops_telemetry
"""

from __future__ import annotations

from alembic import op

revision = "0032_service_accounts"
down_revision = "0031_ops_telemetry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A service account needs its own usage tier: it is shared by everyone on
    # its surface, so the 5/day free counter would be hit by lunchtime. Widening
    # `plan` rather than `role` is deliberate — a machine identity must never be
    # able to become an admin, which is what role-based exemption would invite.
    op.execute("ALTER TABLE app.users DROP CONSTRAINT IF EXISTS users_plan_check")
    op.execute(
        "ALTER TABLE app.users ADD CONSTRAINT users_plan_check "
        "CHECK (plan IN ('free', 'plus', 'pro', 'service'))"
    )

    op.execute(
        "CREATE TABLE IF NOT EXISTS app.service_accounts ("
        "  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
        "  user_id      uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,"
        "  name         text NOT NULL,"
        # key_id is the public half: safe to log, safe to show in a UI list.
        "  key_id       text NOT NULL UNIQUE,"
        # sha256 hex of the secret half. The raw key exists exactly once, in the
        # create response. There is no recovery path, by design.
        "  key_hash     text NOT NULL,"
        "  surface      text NOT NULL CHECK (surface IN ('webhook', 'slack', 'mcp')),"
        "  created_at   timestamptz NOT NULL DEFAULT now(),"
        "  last_used_at timestamptz,"
        "  revoked_at   timestamptz"
        ")"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS service_accounts_user_id_idx ON app.service_accounts (user_id)"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON app.service_accounts TO app_user")

    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0032_service_accounts') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.service_accounts")
    # Normalise before narrowing the CHECK back, mirroring 0031's status rollback.
    op.execute("UPDATE app.users SET plan = 'free' WHERE plan = 'service'")
    op.execute("ALTER TABLE app.users DROP CONSTRAINT IF EXISTS users_plan_check")
    op.execute(
        "ALTER TABLE app.users ADD CONSTRAINT users_plan_check "
        "CHECK (plan IN ('free', 'plus', 'pro'))"
    )
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0032_service_accounts'")
