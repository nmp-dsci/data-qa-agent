"""Fixtures for db tests.

- ``sample_raw_dir`` writes one tiny CSV per dataset matching the §1.3 header
  contracts (no network, no real files touched).
- ``pg_conn`` yields a psycopg connection into an isolated throwaway schema
  (``raw_yfinance`` recreated per test) if a Postgres is reachable via
  ``DATABASE_URL``; otherwise the test is skipped gracefully so ``pytest`` does
  not hard-fail in a CI without Docker.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

# Tiny, contract-accurate fixtures (headers mirror §1.3 / the real AAPL files).
_SAMPLES: dict[str, str] = {
    "eod_prices": (
        "ticker,date,open,high,low,close,adj_close,volume,currency,source,ingested_at\n"
        "AAPL,2024-01-02,185.0,186.0,184.0,185.5,185.4,1000000,USD,yfinance,"
        "2026-06-04T09:59:00+00:00\n"
        "AAPL,2024-01-03,186.0,187.0,185.0,186.5,186.4,1100000,USD,yfinance,"
        "2026-06-04T09:59:00+00:00\n"
    ),
    "corporate_actions": (
        "ticker,date,action_type,value,source,ingested_at\n"
        "AAPL,2024-02-09,dividend,0.24,yfinance,2026-06-04T09:59:00+00:00\n"
    ),
    "balance_sheet": (
        "ticker,statement,freq,period_end,line_item,value,currency,source,ingested_at\n"
        "AAPL,balance_sheet,annual,2023-09-30,TotalAssets,352583000000,USD,yfinance,"
        "2026-06-04T09:59:00+00:00\n"
    ),
    "income_statement": (
        "ticker,statement,freq,period_end,line_item,value,currency,source,ingested_at\n"
        "AAPL,income_statement,annual,2023-09-30,TotalRevenue,383285000000,USD,yfinance,"
        "2026-06-04T09:59:00+00:00\n"
    ),
    "cash_flow": (
        "ticker,statement,freq,period_end,line_item,value,currency,source,ingested_at\n"
        "AAPL,cash_flow,annual,2023-09-30,FreeCashFlow,99584000000,USD,yfinance,"
        "2026-06-04T09:59:00+00:00\n"
    ),
    "company_profile": (
        "ticker,company_name,sector,industry,currency,exchange,country,ingested_at\n"
        "AAPL,Apple Inc.,Technology,Consumer Electronics,USD,NMS,United States,"
        "2026-06-04T09:59:00+00:00\n"
    ),
}

# Expected data-row counts for the fixtures above.
SAMPLE_ROW_COUNTS: dict[str, int] = {
    "eod_prices": 2,
    "corporate_actions": 1,
    "balance_sheet": 1,
    "income_statement": 1,
    "cash_flow": 1,
    "company_profile": 1,
}


@pytest.fixture
def sample_raw_dir(tmp_path: Path) -> Path:
    """A temp landing zone with one timestamped CSV per dataset."""
    ts = "202606040959"
    for dataset, body in _SAMPLES.items():
        ddir = tmp_path / dataset
        ddir.mkdir(parents=True)
        (ddir / f"AAPL_{ts}.csv").write_text(body, encoding="utf-8")
    return tmp_path


@pytest.fixture
def pg_conn() -> Iterator[object]:
    """A psycopg connection with a fresh ``raw_yfinance`` schema, or skip.

    Drops + recreates ``raw_yfinance`` so the test starts clean and does not
    touch any real loaded data. Skips if Postgres is unreachable.
    """
    import psycopg

    from data_qa_agent.db.connection import database_url
    from data_qa_agent.db.migrate import apply_migrations

    try:
        conn = psycopg.connect(database_url(), connect_timeout=3)
    except psycopg.OperationalError as exc:  # pragma: no cover - env dependent
        pytest.skip(f"Postgres not reachable ({exc})")

    try:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS raw_yfinance CASCADE")
        conn.commit()
        apply_migrations(conn)
        yield conn
    finally:
        # Leave a clean slate; never persist test data.
        try:
            with conn.cursor() as cur:
                cur.execute("DROP SCHEMA IF EXISTS raw_yfinance CASCADE")
            conn.commit()
        finally:
            conn.close()
