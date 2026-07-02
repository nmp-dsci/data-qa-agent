"""staging schema grant — agent_ro can query the widened staging tables

Data pipeline refactor. The agent now queries stg_sales/stg_rent directly for
record-level questions (e.g. "top 10 addresses by sale price"), not just the
marts. Per-table SELECT + RLS on stg_sales/stg_rent is granted by the dbt
apply_dataset_rls post_hook at build time (same pattern as marts.*), but
schema-level USAGE isn't something dbt's per-table macro grants — it's a
one-time grant, same as the `GRANT USAGE ON SCHEMA app, marts TO app_user,
agent_ro` in the 0001 baseline.

Revision ID: 0005_staging_schema_grant
Revises: 0004_query_run_tokens
"""

from __future__ import annotations

from alembic import op

revision = "0005_staging_schema_grant"
down_revision = "0004_query_run_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT USAGE ON SCHEMA staging TO agent_ro")
    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0005_staging_schema_grant') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("REVOKE USAGE ON SCHEMA staging FROM agent_ro")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0005_staging_schema_grant'")
