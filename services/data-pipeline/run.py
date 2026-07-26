"""Pipeline entrypoint: dlt ingest -> dbt build.

Derives both dlt's and dbt's Postgres connection from a single ADMIN_DATABASE_URL
(a privileged connection, so dbt-built marts are owned by the admin role and RLS
applies to app_user/agent_ro). Runs the dlt ingestion, then `dbt build` (models +
tests). Idempotent — safe to re-run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
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


def _dbt_test_counts(dbt_dir: Path) -> tuple[int | None, int | None]:
    """(passed, total) dbt tests from run_results.json, or (None, None).

    Read from dbt's own artifact rather than parsed out of stdout, so a change in
    dbt's log format can't silently turn "37/37" into "no data" on the deck.
    """
    try:
        results = json.loads((dbt_dir / "target" / "run_results.json").read_text())
    except (OSError, ValueError):
        return None, None
    tests = [r for r in results.get("results", []) if ".test." in str(r.get("unique_id", ""))]
    if not tests:
        return None, None
    passed = sum(1 for r in tests if r.get("status") == "pass")
    return passed, len(tests)


def _row_counts() -> dict[str, int]:
    """Row counts for the marts the app answers over — the freshness panel's detail.

    Best-effort: a counting failure must never fail a pipeline that has already
    built the data.
    """
    import psycopg2

    tables = ("marts.property_sales", "marts.property_rent", "marts.property_yield")
    counts: dict[str, int] = {}
    try:
        with psycopg2.connect(os.environ["DESTINATION__POSTGRES__CREDENTIALS"]) as conn:
            with conn.cursor() as cur:
                for table in tables:
                    try:
                        # Identifiers are this module's own constants, never input.
                        cur.execute(f"SELECT count(*) FROM {table}")  # noqa: S608
                        row = cur.fetchone()
                        counts[table.split(".", 1)[1]] = int(row[0]) if row else 0
                    except Exception:  # noqa: BLE001 — a missing mart is not fatal here
                        conn.rollback()
    except Exception as exc:  # noqa: BLE001
        print(f"==> row counts unavailable: {exc}")
    return counts


def _record_run(*, status: str, started: float, dbt_dir: Path) -> None:
    """Post this run to app.pipeline_runs via the ops ingest endpoint (s32 W2).

    Data freshness is the metric whose absence caused a real prod outage: the
    marts froze for 12 days while the app kept deploying, `marts.property_yield`
    was never built, and the Explore tab 500'd on every load. Nothing measured
    "how old is the data" because nothing wrote it down. Now the pipeline itself
    does, on every run, so the deck's freshness lamp needs no AWS call.

    Entirely best-effort — a telemetry write must never fail a pipeline that has
    already built the marts. The script it calls exits 0 even on failure.
    """
    if not os.environ.get("OPS_INGEST_TOKEN"):
        return
    passed, total = _dbt_test_counts(dbt_dir)
    # In the image the writer sits at ./scripts/ops_ingest.py (see Dockerfile);
    # running from a checkout it is at the repo root. Try both rather than
    # assuming, so `make pipeline` and the ECS job behave the same.
    candidates = (HERE / "scripts" / "ops_ingest.py", HERE.parents[1] / "scripts" / "ops_ingest.py")
    ingest_script = next((p for p in candidates if p.exists()), None)
    if ingest_script is None:
        print("==> ops_ingest.py not found; pipeline run not recorded")
        return
    args = [
        sys.executable,
        str(ingest_script),
        "pipeline-run",
        "--status",
        status,
        "--duration-s",
        str(int(time.time() - started)),
        "--marts-refreshed-at",
        datetime.now(UTC).isoformat(),
        "--row-counts",
        json.dumps(_row_counts()),
        "--source",
        os.environ.get("PIPELINE_SOURCE", "sample"),
    ]
    if passed is not None and total is not None:
        args += ["--dbt-pass", str(passed), "--dbt-total", str(total)]
    try:
        subprocess.run(args, check=False, timeout=60)
    except Exception as exc:  # noqa: BLE001
        print(f"==> pipeline run not recorded: {exc}")


def main() -> None:
    started = time.time()
    _configure_connection()
    _wake_database()
    dbt_dir = HERE / "dbt"

    try:
        # 1) dlt ingest (import after env is set so dlt picks up credentials).
        import ingest

        ingest.main()

        # 2) dbt build (models + tests). Docs are generated so the agent can read
        #    the manifest.
        env = {**os.environ, "DBT_PROFILES_DIR": str(dbt_dir)}
        for cmd in (["dbt", "build"], ["dbt", "docs", "generate", "--no-compile"]):
            print(f"==> {' '.join(cmd)}")
            result = subprocess.run([*cmd, "--project-dir", str(dbt_dir)], env=env)
            if result.returncode != 0:
                # Record the failure too: a pipeline that failed is exactly when
                # the deck's freshness lamp needs to go red.
                _record_run(status="failed", started=started, dbt_dir=dbt_dir)
                sys.exit(result.returncode)
    except SystemExit:
        raise
    except Exception:
        _record_run(status="failed", started=started, dbt_dir=dbt_dir)
        raise

    _record_run(status="success", started=started, dbt_dir=dbt_dir)
    print("==> pipeline complete.")


if __name__ == "__main__":
    main()
