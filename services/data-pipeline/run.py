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
from pathlib import Path
from urllib.parse import urlparse

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

    # dbt profile (profiles.yml reads these env vars).
    os.environ.setdefault("DBT_HOST", parts.hostname or "db")
    os.environ.setdefault("DBT_PORT", str(parts.port or 5432))
    os.environ.setdefault("DBT_USER", parts.username or "postgres")
    os.environ.setdefault("DBT_PASSWORD", parts.password or "postgres")
    os.environ.setdefault("DBT_DBNAME", parts.path.lstrip("/") or "dataqa")


def main() -> None:
    _configure_connection()

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
