"""phase 0 baseline — schemas, roles, tables, RLS, seed

Applies the canonical DDL that used to run via Postgres' init scripts. The SQL
files are the single source of truth for the schema; this baseline revision just
executes them under Alembic so migrations are versioned and repeatable (local
and cloud run the same `alembic upgrade head`). Housing *data* is loaded
separately by the seed step — it is pipeline output, not schema.

Revision ID: 0001_phase0_init
Revises:
"""

from __future__ import annotations

import os
from pathlib import Path

from alembic import op

revision = "0001_phase0_init"
down_revision = None
branch_labels = None
depends_on = None

# Where the canonical .sql files live (bundled into the image next to this pkg).
SQL_DIR = Path(os.environ.get("SCHEMA_SQL_DIR", str(Path(__file__).resolve().parents[1] / "sql")))


def _apply(filename: str) -> None:
    sql = (SQL_DIR / filename).read_text()
    # exec_driver_sql sends the script with no bound params, so psycopg uses the
    # simple query protocol and runs the multi-statement file as one batch.
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _apply("01_schema.sql")
    _apply("02_rls.sql")
    _apply("03_seed.sql")
    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0001_phase0_init') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    # Drops the application schemas; login roles are left in place intentionally.
    for schema in ("marts", "staging", "raw", "app"):
        op.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
