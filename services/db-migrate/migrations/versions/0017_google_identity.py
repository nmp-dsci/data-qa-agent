"""generalise external identity: entra_oid -> external_id + auth_provider (Google sign-in)

Google Sign-in (s11) replaces Entra. The users table keyed its external subject
on `entra_oid`; this renames it to a provider-agnostic `external_id` and adds
`auth_provider`, so a JIT-provisioned identity is unique per (provider, subject).
Dev-stub users keep a null external_id.

Revision ID: 0017_google_identity
Revises: 0016_user_plans
"""

from __future__ import annotations

from alembic import op

revision = "0017_google_identity"
down_revision = "0016_user_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE app.users RENAME COLUMN entra_oid TO external_id")
    op.execute("ALTER TABLE app.users ADD COLUMN IF NOT EXISTS auth_provider text")
    # Any pre-existing external subjects were Entra; dev-stub users stay null.
    op.execute("UPDATE app.users SET auth_provider = 'entra' WHERE external_id IS NOT NULL")
    # Replace the single-column unique (auto-named on the old column) with a
    # composite unique so the same subject can't collide within a provider, and
    # so the JIT upsert can ON CONFLICT (auth_provider, external_id).
    op.execute("ALTER TABLE app.users DROP CONSTRAINT IF EXISTS users_entra_oid_key")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS users_provider_external_id_key "
        "ON app.users (auth_provider, external_id)"
    )
    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0017_google_identity') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS app.users_provider_external_id_key")
    op.execute("ALTER TABLE app.users DROP COLUMN IF EXISTS auth_provider")
    op.execute("ALTER TABLE app.users RENAME COLUMN external_id TO entra_oid")
    op.execute("ALTER TABLE app.users ADD CONSTRAINT users_entra_oid_key UNIQUE (entra_oid)")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0017_google_identity'")
