"""propertyiq_staging — read the clean property + economic data from propertyiq over postgres_fdw

Platform M3 follow-up (propertyiq_getdata plan s03, decision D7). The
``propertyiq_getdata`` project now lands NSW sales, rental bonds and the
ABS/RBA economic series in its own database ``propertyiq`` on the central
cluster and cleans them to record grain in ``propertyiq.staging``. That is the
shared layer every app consumes; this project stops ingesting the monolith
CSVs itself (dlt, ``raw.property_sales`` / ``raw.property_rent``) and instead
reads ``propertyiq_staging.*`` -- foreign tables over ``postgres_fdw`` -- and
keeps building its own marts + RLS from them with dbt.

What this migration does, as the superuser ``nmp`` inside ``dataqa`` only:

* ``CREATE EXTENSION postgres_fdw`` (declared in the platform registry too).
* Foreign server ``propertyiq`` -> database ``propertyiq`` on the same cluster
  (host from ``PROPERTYIQ_FDW_HOST``, default ``postgres`` = the compose-network
  name; ``localhost`` when migrating from the host).
* One user mapping FOR PUBLIC as the platform's read-only role
  ``propertyiq_ro`` (password from ``PROPERTYIQ_RO_PASSWORD``, default = role
  name per the platform's local convention). Read-only by construction, so
  letting every dataqa role use it is safe; the app's RLS still applies to the
  local marts, which is what app_user / agent_ro query.
* Schema ``propertyiq_staging`` + ``IMPORT FOREIGN SCHEMA staging``.
* Drops the two dlt landing tables ``raw.property_sales`` / ``raw.property_rent``
  (dbt rebuilds ``staging.*`` and ``marts.*`` from the foreign tables on the
  next ``make pipeline``). Back up first: ``make -C ../nmp-central-ai db-backup DB=dataqa``.

Re-run ``IMPORT FOREIGN SCHEMA`` when propertyiq adds a staging table:
``make migrate`` is idempotent here (schema is dropped and re-imported).

Revision ID: 0040_propertyiq_staging_fdw
Revises: 0039_eval_loop_s49
"""

from __future__ import annotations

import os

from alembic import op

revision = "0040_propertyiq_staging_fdw"
down_revision = "0039_eval_loop_s49"
branch_labels = None
depends_on = None


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def upgrade() -> None:
    host = os.environ.get("PROPERTYIQ_FDW_HOST", "postgres")
    port = os.environ.get("PROPERTYIQ_FDW_PORT", "5432")
    dbname = os.environ.get("PROPERTYIQ_FDW_DBNAME", "propertyiq")
    ro_user = os.environ.get("PROPERTYIQ_RO_USER", "propertyiq_ro")
    ro_password = os.environ.get("PROPERTYIQ_RO_PASSWORD", ro_user)

    op.execute("CREATE EXTENSION IF NOT EXISTS postgres_fdw")
    op.execute("DROP SERVER IF EXISTS propertyiq CASCADE")  # drops mappings + foreign tables; re-created below
    op.execute(
        f"CREATE SERVER propertyiq FOREIGN DATA WRAPPER postgres_fdw "
        f"OPTIONS (host {_q(host)}, port {_q(port)}, dbname {_q(dbname)}, fetch_size '10000')"
    )
    op.execute(
        f"CREATE USER MAPPING FOR PUBLIC SERVER propertyiq OPTIONS (user {_q(ro_user)}, password {_q(ro_password)})"
    )
    op.execute("CREATE SCHEMA IF NOT EXISTS propertyiq_staging")
    op.execute("IMPORT FOREIGN SCHEMA staging FROM SERVER propertyiq INTO propertyiq_staging")
    # The app roles only ever read the local, RLS-scoped marts, but the admin
    # SQL editor (admin_ro, BYPASSRLS) and the agent's schema doc benefit from
    # seeing the shared layer directly.
    op.execute("GRANT USAGE ON SCHEMA propertyiq_staging TO app_user, agent_ro, admin_ro")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA propertyiq_staging TO app_user, agent_ro, admin_ro")
    op.execute("GRANT USAGE ON FOREIGN SERVER propertyiq TO app_user, agent_ro, admin_ro")

    # The dlt landing tables. Nothing reads them once staging comes from propertyiq.
    op.execute("DROP TABLE IF EXISTS raw.property_sales CASCADE")
    op.execute("DROP TABLE IF EXISTS raw.property_rent CASCADE")
    op.execute("DROP TABLE IF EXISTS raw._dlt_loads, raw._dlt_pipeline_state, raw._dlt_version CASCADE")

    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0040_propertyiq_staging_fdw') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    # The raw tables are not restored: re-populating them needs the retired dlt
    # ingest. Downgrading only removes the fdw wiring.
    op.execute("DROP SCHEMA IF EXISTS propertyiq_staging CASCADE")
    op.execute("DROP SERVER IF EXISTS propertyiq CASCADE")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0040_propertyiq_staging_fdw'")
