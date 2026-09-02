"""promotions — append-only champion/challenger promotion history (s43 M2)

The MLOps plane (MLflow) holds the @champion/@challenger aliases, but the
promotion *record* lives here so the app can render the history without a
tracking server — the same source-of-truth split the whole s43 build follows
(Postgres first, MLflow as the mirror/mechanics). One row per promotion, never
updated or deleted: the alias moves, the evidence stays.

Revision ID: 0035_promotions
Revises: 0034_queue_telemetry
"""

from __future__ import annotations

from alembic import op

revision = "0035_promotions"
down_revision = "0034_queue_telemetry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS app.promotions ("
        "  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
        "  model_name text NOT NULL,"
        "  from_version text,"
        "  to_version text NOT NULL,"
        "  agent_version_id uuid REFERENCES app.agent_versions(id),"
        "  verdict jsonb NOT NULL DEFAULT '{}'::jsonb,"
        "  created_at timestamptz NOT NULL DEFAULT now()"
        ")"
    )
    # Ops metadata, not user data: readable by the app roles, written by the
    # promotion CLI (which runs as the migration owner via psql). No RLS — there
    # are no per-user rows here.
    op.execute("GRANT SELECT ON app.promotions TO app_user")
    op.execute("GRANT SELECT ON app.promotions TO admin_ro")
    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0035_promotions') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.promotions")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0035_promotions'")
