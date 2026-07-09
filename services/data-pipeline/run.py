"""Pipeline entrypoint: dlt ingest -> dbt build.

Derives both dlt's and dbt's Postgres connection from a single ADMIN_DATABASE_URL
(a privileged connection, so dbt-built marts are owned by the admin role and RLS
applies to app_user/agent_ro). Runs the dlt ingestion, then `dbt build` (models +
tests). Idempotent — safe to re-run.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

HERE = Path(__file__).resolve().parent


def _configure_connection() -> None:
    url = os.environ.get("ADMIN_DATABASE_URL")
    if not url:
        raise SystemExit("ADMIN_DATABASE_URL is required")
    url = url.replace("postgresql+psycopg://", "postgresql://").replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    parts = urlparse(url)

    # dlt destination (reads DESTINATION__POSTGRES__CREDENTIALS).
    os.environ["DESTINATION__POSTGRES__CREDENTIALS"] = url

    # dbt profile (profiles.yml reads these env vars). urlparse returns the
    # userinfo verbatim, so percent-decode it — cloud passwords (s12) carry
    # URL-special characters and arrive encoded. libpq-based consumers (dlt)
    # decode the full URL themselves; dbt gets the parts, so we decode here.
    os.environ.setdefault("DBT_HOST", parts.hostname or "db")
    os.environ.setdefault("DBT_PORT", str(parts.port or 5432))
    os.environ.setdefault("DBT_USER", unquote(parts.username or "postgres"))
    os.environ.setdefault("DBT_PASSWORD", unquote(parts.password or "postgres"))
    os.environ.setdefault("DBT_DBNAME", parts.path.lstrip("/") or "dataqa")


def _wake_database() -> None:
    """Connect-with-retry until the DB accepts (Aurora auto-pause resume, s12).

    A paused Serverless v2 cluster refuses/times out the first connections
    while it resumes (~15-60s). dlt/dbt don't retry, so absorb it here.
    """
    import psycopg2  # dlt[postgres] ships it

    url = os.environ["DESTINATION__POSTGRES__CREDENTIALS"]
    last: Exception | None = None
    for attempt in range(1, 25):
        try:
            psycopg2.connect(url, connect_timeout=15).close()
            print(f"==> database awake (attempt {attempt})")
            return
        except psycopg2.OperationalError as e:  # refused/timeout while resuming
            last = e
            print(f"==> waiting for database (attempt {attempt}): {str(e).splitlines()[0][:90]}")
            time.sleep(10)
    raise SystemExit(f"database never became reachable: {last}")


def main() -> None:
    _configure_connection()
    _wake_database()

    # 1) dlt ingest (import after env is set so dlt picks up credentials).
    import ingest

    ingest.main()

    # 2) dbt build (models + tests). Docs are generated so the agent can read the manifest.
    dbt_dir = HERE / "dbt"
    env = {**os.environ, "DBT_PROFILES_DIR": str(dbt_dir)}
    for cmd in (["dbt", "build"], ["dbt", "docs", "generate", "--no-compile"]):
        print(f"==> {' '.join(cmd)}")
        result = subprocess.run([*cmd, "--project-dir", str(dbt_dir)], env=env)
        if result.returncode != 0:
            sys.exit(result.returncode)

    print("==> pipeline complete.")


if __name__ == "__main__":
    main()
