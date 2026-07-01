"""Post-migration data seed + role-password rotation.

Runs after `alembic upgrade head`. Idempotent: it loads the housing CSV into
raw.housing and builds marts.housing only when marts.housing is empty, so it is
safe to re-run. In the cloud it also rotates the app/agent role passwords to the
Key Vault values (local dev keeps the defaults baked into the schema).

Housing loading is a Phase-0 stand-in for the dlt + dbt pipeline (Phase 2b).
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
from psycopg import sql

RAW_COPY = (
    "COPY raw.housing (id, suburb, property_type, price, bedrooms, bathrooms, "
    "car_spaces, land_size_sqm, year_built, sale_date) "
    "FROM STDIN WITH (FORMAT csv, HEADER true)"
)


def _conninfo() -> str:
    url = os.environ.get("ADMIN_DATABASE_URL")
    if not url:
        raise SystemExit("ADMIN_DATABASE_URL is required")
    # psycopg wants a libpq URL; strip any SQLAlchemy driver suffix.
    return url.replace("postgresql+psycopg://", "postgresql://").replace(
        "postgresql+asyncpg://", "postgresql://"
    )


def _load_housing(conn: psycopg.Connection) -> None:
    already = conn.execute("SELECT count(*) FROM marts.housing").fetchone()
    if already and already[0] > 0:
        print(f"==> Housing already loaded ({already[0]} rows) — skipping.")
        return

    csv_path = Path(os.environ.get("HOUSING_CSV", "/data/incoming/housing.csv"))
    if not csv_path.exists():
        print(f"==> No CSV at {csv_path} — skipping housing load.")
        return

    print(f"==> Loading housing data from {csv_path}")
    with conn.cursor() as cur, cur.copy(RAW_COPY) as copy:
        copy.write(csv_path.read_bytes())
    conn.execute(Path(__file__).with_name("seed_housing.sql").read_text())
    count = conn.execute("SELECT count(*) FROM marts.housing").fetchone()
    print(f"==> marts.housing now has {count[0] if count else 0} rows.")


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
        _load_housing(conn)
        _rotate_passwords(conn)
        conn.commit()
    print("==> Seed complete.")


if __name__ == "__main__":
    main()
