"""Orchestration CLI (§4) — chains EXTRACT -> LOAD -> TRANSFORM for one run.

    uv run python -m data_qa_agent.pipeline run --tickers AAPL
    uv run python -m data_qa_agent.pipeline run --universe sp500 --sleep 0.5

Each stage is the same code path used by its standalone CLI:
  * EXTRACT  -> data_qa_agent.ingest.run_extract.main
  * LOAD     -> data_qa_agent.db.migrate.apply_migrations + db.run_load.main
  * TRANSFORM-> `dbt build` in the dbt/ project (subprocess)
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys

from data_qa_agent.config import REPO_ROOT
from data_qa_agent.db import migrate, run_load
from data_qa_agent.ingest import run_extract

log = logging.getLogger("data_qa_agent.pipeline")

DBT_DIR = REPO_ROOT / "dbt"


def _extract_argv(args: argparse.Namespace) -> list[str]:
    argv: list[str] = []
    if args.tickers:
        argv += ["--tickers", args.tickers]
    if args.universe:
        argv += ["--universe", args.universe]
    argv += ["--years", str(args.years)]
    if args.datasets:
        argv += ["--datasets", args.datasets]
    if args.force:
        argv += ["--force"]
    if args.force_dataset:
        argv += ["--force-dataset", args.force_dataset]
    if args.sleep:
        argv += ["--sleep", str(args.sleep)]
    if args.dry_run:
        argv += ["--dry-run"]
    return argv


def _load_argv(args: argparse.Namespace) -> list[str]:
    return ["--datasets", args.datasets or "all"]


def run(args: argparse.Namespace) -> int:
    # 1. EXTRACT
    log.info("=== stage 1/3: EXTRACT ===")
    rc = run_extract.main(_extract_argv(args))
    if rc != 0:
        log.error("EXTRACT failed (rc=%s); aborting pipeline.", rc)
        return rc
    if args.dry_run:
        log.info("--dry-run: stopping after EXTRACT (no load/transform).")
        return 0

    # 2. LOAD (migrate is idempotent; safe to run every time)
    log.info("=== stage 2/3: LOAD ===")
    migrate.apply_migrations()
    rc = run_load.main(_load_argv(args))
    if rc != 0:
        log.error("LOAD failed (rc=%s); aborting pipeline.", rc)
        return rc

    # 3. TRANSFORM (dbt build = run + test + snapshot + seed)
    log.info("=== stage 3/3: TRANSFORM (dbt build) ===")
    proc = subprocess.run(
        ["uv", "run", "dbt", "build", "--profiles-dir", "."],
        cwd=DBT_DIR,
    )
    if proc.returncode != 0:
        log.error("TRANSFORM (dbt build) failed (rc=%s).", proc.returncode)
        return proc.returncode

    log.info("pipeline complete.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="data_qa_agent.pipeline")
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="run the full ELT pipeline for the given tickers")
    r.add_argument("--tickers", help="comma-separated explicit tickers, e.g. AAPL,MSFT")
    r.add_argument("--universe", help="ticker universe, e.g. 'sp500'")
    r.add_argument("--years", type=int, default=10, help="years of EOD history (default 10)")
    r.add_argument("--datasets", help="comma-separated datasets (default: all)")
    r.add_argument("--force", action="store_true", help="re-pull full window for all datasets")
    r.add_argument("--force-dataset", help="comma-separated datasets to force re-pull")
    r.add_argument("--sleep", type=float, default=0.0, help="seconds between tickers")
    r.add_argument("--dry-run", action="store_true", help="extract deltas only, write nothing")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = build_parser().parse_args(argv)
    if args.command == "run":
        return run(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
