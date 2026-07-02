"""Post-migration role-password rotation.

Runs after `alembic upgrade head`. In the cloud it rotates the app/agent role
passwords to the Key Vault values (local dev keeps the defaults baked into the
schema). Dataset *data* is loaded separately by the data-pipeline (dlt + dbt).
"""

from __future__ import annotations

import os

import psycopg
from psycopg import sql


def _conninfo() -> str:
    url = os.environ.get("ADMIN_DATABASE_URL")
    if not url:
        raise SystemExit("ADMIN_DATABASE_URL is required")
    # psycopg wants a libpq URL; strip any SQLAlchemy driver suffix.
    return url.replace("postgresql+psycopg://", "postgresql://").replace(
        "postgresql+asyncpg://", "postgresql://"
    )


def _rotate_passwords(conn: psycopg.Connection) -> None:
    app_pw = os.environ.get("APP_USER_PW")
    agent_pw = os.environ.get("AGENT_RO_PW")
    if app_pw:
        conn.execute(sql.SQL("ALTER ROLE app_user PASSWORD {}").format(sql.Literal(app_pw)))
    if agent_pw:
        conn.execute(sql.SQL("ALTER ROLE agent_ro PASSWORD {}").format(sql.Literal(agent_pw)))
    if app_pw or agent_pw:
        print("==> Rotated app/agent role passwords.")


def main() -> None:
    with psycopg.connect(_conninfo()) as conn:
        _rotate_passwords(conn)
        conn.commit()
    print("==> Post-migration seed complete.")


if __name__ == "__main__":
    main()
