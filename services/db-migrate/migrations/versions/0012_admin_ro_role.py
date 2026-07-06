"""admin_ro role — a read-only, RLS-bypassing role for the admin SQL editor

Admins need to query EVERY table in the database from the SQL editor — including
internal ``app.*`` tables (eval_cases, answer_feedback, …) that were never granted
to the RLS-scoped ``agent_ro`` role, which is why ``select * from app.eval_cases``
returned "permission denied for table eval_cases". Rather than granting agent_ro
broad access and adding an admin-only RLS policy to every table, we add a dedicated
read role:

  ``admin_ro`` — NOSUPERUSER, **BYPASSRLS**, LOGIN, SELECT on every schema
  (app, marts, staging, raw). Read-only: only SELECT is granted, so an admin still
  can't write through the editor. BYPASSRLS means an admin sees all rows.

The data-agent routes SQL-editor requests from ``role == "admin"`` through
admin_ro; everyone else stays on agent_ro (marts + staging, RLS-scoped). Future
marts/staging/raw tables (dbt/dlt rebuild them every pipeline run, as the same
privileged ``postgres`` role that runs these migrations) are covered by
ALTER DEFAULT PRIVILEGES, so admin_ro keeps access across rebuilds without a
per-run re-grant.

Revision ID: 0012_admin_ro_role
Revises: 0011_query_run_channel
"""

from __future__ import annotations

from alembic import op

revision = "0012_admin_ro_role"
down_revision = "0011_query_run_channel"
branch_labels = None
depends_on = None

_SCHEMAS = ("app", "marts", "staging", "raw")


def upgrade() -> None:
    # Idempotent role creation. The dev password mirrors the agent_ro/app_user
    # pattern (compose default); in Azure the role + password are provisioned
    # out-of-band and this CREATE is a no-op because the role already exists.
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'admin_ro') THEN "
        "CREATE ROLE admin_ro LOGIN PASSWORD 'admin_pw' NOSUPERUSER BYPASSRLS; "
        "END IF; END $$"
    )
    for schema in _SCHEMAS:
        op.execute(f"GRANT USAGE ON SCHEMA {schema} TO admin_ro")
        # Existing tables now …
        op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO admin_ro")
        # … and future ones. These migrations run as the same privileged role
        # (ADMIN_DATABASE_URL = postgres) that dbt/dlt use to (re)create marts,
        # staging, and raw tables, so default privileges cover every rebuild.
        op.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT SELECT ON TABLES TO admin_ro"
        )
    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0012_admin_ro_role') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    for schema in _SCHEMAS:
        op.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} REVOKE SELECT ON TABLES FROM admin_ro"
        )
        op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA {schema} FROM admin_ro")
        op.execute(f"REVOKE USAGE ON SCHEMA {schema} FROM admin_ro")
    op.execute("DROP ROLE IF EXISTS admin_ro")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0012_admin_ro_role'")
