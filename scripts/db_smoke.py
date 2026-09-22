"""Zero-LLM proof that the central database serves this project (platform M3).

Run by the platform's `check_databases.py` (which exports this project's URLs) and by
`make db-smoke`. Checks, as each role the project uses:

  admin (ADMIN_DATABASE_URL, the platform superuser)  Alembic is at head; the four schemas exist
  app_user (DATABASE_URL)                              can read marts.property_sales as user1 (rows > 0)
                                                       and sees ZERO rows as user2 (RLS: no nsw_sales grant)
  agent_ro (AGENT_RO_DATABASE_URL)                     can SELECT, cannot INSERT
  admin_ro (ADMIN_RO_DATABASE_URL)                     sees every housing row (BYPASSRLS)

Exit 1 with one line per failure. Stdlib + psycopg only (`uv run --with "psycopg[binary]"`).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg

SCHEMAS = ("app", "raw", "staging", "marts")
VERSIONS = (
    Path(__file__).resolve().parents[1] / "services" / "db-migrate" / "migrations" / "versions"
)


def url(var: str) -> str:
    raw = os.environ.get(var)
    if not raw:
        sys.exit(f"{var} is not set — `make -C ../nmp-central-ai db-urls` prints the .env block")
    return raw.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg://", "postgresql://"
    )


def head_revision() -> str:
    """The Alembic head from the migration files: the revision nothing names as down_revision."""
    revs: dict[str, str | None] = {}
    for f in VERSIONS.glob("*.py"):
        text = f.read_text()
        rev = down = None
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("revision") and "=" in s and not s.startswith("revision_"):
                rev = s.split("=", 1)[1].strip().strip("\"'")
            elif s.startswith("down_revision") and "=" in s:
                v = s.split("=", 1)[1].strip()
                down = None if v == "None" else v.strip("\"'")
        if rev:
            revs[rev] = down
    downs = {d for d in revs.values() if d}
    heads = [r for r in revs if r not in downs]
    if len(heads) != 1:
        sys.exit(f"could not determine a single Alembic head from {VERSIONS}: {heads}")
    return heads[0]


def one(conn: psycopg.Connection, sql: str, *params: object) -> object:
    row = conn.execute(sql, params or None).fetchone()
    return row[0] if row else None


def main() -> int:
    failures: list[str] = []
    ok: list[str] = []

    with psycopg.connect(url("ADMIN_DATABASE_URL"), connect_timeout=5) as admin:
        have = {str(r[0]) for r in admin.execute("select nspname from pg_namespace").fetchall()}
        missing = [s for s in SCHEMAS if s not in have]
        (failures if missing else ok).append(
            f"schemas missing: {missing}" if missing else f"schemas {', '.join(SCHEMAS)} present"
        )
        current = one(admin, "select version_num from public.alembic_version")
        head = head_revision()
        (ok if current == head else failures).append(f"alembic current={current} head={head}")
        user1 = one(admin, "select id from app.users where username = 'user1'")
        user2 = one(admin, "select id from app.users where username = 'user2'")
        total = one(admin, "select count(*) from marts.property_sales")
        if not (user1 and user2):
            failures.append("seed users user1/user2 missing")
        ok.append(f"marts.property_sales rows as admin: {total}")

    if user1 and user2:
        with psycopg.connect(url("DATABASE_URL"), connect_timeout=5) as app:
            with app.transaction():
                app.execute("select set_config('app.current_user_id', %s, true)", (str(user1),))
                n1 = one(app, "select count(*) from marts.property_sales")
            with app.transaction():
                app.execute("select set_config('app.current_user_id', %s, true)", (str(user2),))
                n2 = one(app, "select count(*) from marts.property_sales")
            (ok if isinstance(n1, int) and n1 > 0 else failures).append(
                f"app_user as user1 sees {n1} property_sales rows"
            )
            (ok if n2 == 0 else failures).append(
                f"app_user as user2 sees {n2} property_sales rows (RLS wants 0)"
            )

    with psycopg.connect(url("AGENT_RO_DATABASE_URL"), connect_timeout=5) as agent:
        one(agent, "select count(*) from app.datasets")
        try:
            agent.execute("insert into app.datasets (slug, name) values ('__smoke', '__smoke')")
            agent.rollback()
            failures.append("agent_ro could INSERT into app.datasets")
        except psycopg.errors.InsufficientPrivilege:
            agent.rollback()
            ok.append("agent_ro can SELECT, cannot INSERT")
        except psycopg.Error as exc:  # any other refusal still means no write happened
            agent.rollback()
            ok.append(f"agent_ro INSERT refused ({type(exc).__name__})")

    with psycopg.connect(url("ADMIN_RO_DATABASE_URL"), connect_timeout=5) as ro:
        n = one(ro, "select count(*) from marts.property_sales")
        (ok if n == total else failures).append(
            f"admin_ro sees {n} property_sales rows (BYPASSRLS; admin saw {total})"
        )

    for line in ok:
        print(f"  ok   {line}")
    for line in failures:
        print(f"  FAIL {line}")
    print(
        "central database serves data-qa-agent"
        if not failures
        else f"{len(failures)} check(s) failed"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
