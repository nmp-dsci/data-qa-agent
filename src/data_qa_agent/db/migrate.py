"""Apply the ``raw_yfinance`` DDL idempotently (spec §2.2 / §2.4).

The DDL uses ``CREATE ... IF NOT EXISTS`` throughout, so running this repeatedly
is a no-op. Invoke as ``uv run python -m data_qa_agent.db.migrate``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import psycopg

from .connection import connect

logger = logging.getLogger(__name__)

DDL_PATH = Path(__file__).resolve().parent / "ddl" / "raw_yfinance.sql"


def read_ddl() -> str:
    """Return the raw_yfinance DDL SQL text."""
    return DDL_PATH.read_text(encoding="utf-8")


def apply_migrations(conn: psycopg.Connection | None = None) -> None:
    """Execute the DDL against ``conn`` (or a fresh connection) in one transaction."""
    sql = read_ddl()
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
        return
    with connect() as own:
        with own.cursor() as cur:
            cur.execute(sql)
        own.commit()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    apply_migrations()
    logger.info("applied raw_yfinance DDL from %s", DDL_PATH)
    print("raw_yfinance schema + tables/indexes are up to date.")


if __name__ == "__main__":
    main()
