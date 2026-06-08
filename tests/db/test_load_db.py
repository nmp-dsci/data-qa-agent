"""DB-backed tests for the append-only load (load-once + append-only proof).

These use the ``pg_conn`` fixture, which runs against a real local Postgres in
an isolated (dropped + recreated) ``raw_yfinance`` schema, and SKIPS when no
Postgres is reachable — so ``pytest`` stays green in a CI without Docker.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import psycopg

from data_qa_agent.db.load import (
    DATASET_TARGETS,
    already_loaded,
    discover_files,
    run_load,
)

from .conftest import SAMPLE_ROW_COUNTS


def _count(conn: psycopg.Connection, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM raw_yfinance.{table}")
        row = cur.fetchone()
        assert row is not None
        return int(row[0])


def _audit_rows(conn: psycopg.Connection) -> list[tuple[str, str, int, str]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT dataset, source_file, rows_inserted, status "
            "FROM raw_yfinance._load_audit ORDER BY dataset"
        )
        return [(r[0], r[1], r[2], r[3]) for r in cur.fetchall()]


def test_load_appends_all_files_with_lineage(
    pg_conn: psycopg.Connection, sample_raw_dir: Path
) -> None:
    result = run_load(raw_dir=sample_raw_dir, conn=pg_conn)
    assert result.counts == {"ok": 6, "skipped": 0, "failed": 0}

    # Per-table counts equal the fixture row counts (financials all share a table).
    assert _count(pg_conn, "eod_prices") == SAMPLE_ROW_COUNTS["eod_prices"]
    assert _count(pg_conn, "corporate_actions") == SAMPLE_ROW_COUNTS["corporate_actions"]
    assert _count(pg_conn, "company_profile") == SAMPLE_ROW_COUNTS["company_profile"]
    fin_expected = (
        SAMPLE_ROW_COUNTS["balance_sheet"]
        + SAMPLE_ROW_COUNTS["income_statement"]
        + SAMPLE_ROW_COUNTS["cash_flow"]
    )
    assert _count(pg_conn, "financial_statements") == fin_expected

    # Every row has load lineage populated.
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM raw_yfinance.eod_prices "
            "WHERE load_id IS NULL OR source_file IS NULL "
            "OR _loaded_at IS NULL OR _row_id IS NULL"
        )
        bad = cur.fetchone()
        assert bad is not None and bad[0] == 0

    # One ok audit row per loaded file (6 files; 3 financial -> financial_statements).
    audit = _audit_rows(pg_conn)
    assert len(audit) == 6
    assert all(status == "ok" for *_, status in audit)


def test_load_once_idempotent(pg_conn: psycopg.Connection, sample_raw_dir: Path) -> None:
    first = run_load(raw_dir=sample_raw_dir, conn=pg_conn)
    assert first.counts["ok"] == 6
    before = {t: _count(pg_conn, t) for t in
              ("eod_prices", "corporate_actions", "financial_statements", "company_profile")}

    second = run_load(raw_dir=sample_raw_dir, conn=pg_conn)
    assert second.counts == {"ok": 0, "skipped": 6, "failed": 0}
    assert second.rows_inserted == 0
    after = {t: _count(pg_conn, t) for t in before}
    assert after == before  # counts unchanged on re-run
    assert len(_audit_rows(pg_conn)) == 6  # no duplicate audit rows


def test_append_only_second_copy_increases_count(
    pg_conn: psycopg.Connection, sample_raw_dir: Path
) -> None:
    run_load(raw_dir=sample_raw_dir, conn=pg_conn)
    baseline = _count(pg_conn, "company_profile")

    # A second timestamped copy of the same file is a NEW source_file -> appends.
    src = sample_raw_dir / "company_profile" / "AAPL_202606040959.csv"
    dst = sample_raw_dir / "company_profile" / "AAPL_202606041000.csv"
    shutil.copyfile(src, dst)

    result = run_load(["company_profile"], raw_dir=sample_raw_dir, conn=pg_conn)
    assert result.counts["ok"] == 1  # new file loaded
    assert result.counts["skipped"] == 1  # original skipped
    # Append-only: count goes UP, dedup is NOT this stage's job.
    assert _count(pg_conn, "company_profile") == baseline + SAMPLE_ROW_COUNTS["company_profile"]


def test_already_loaded_reflects_audit(
    pg_conn: psycopg.Connection, sample_raw_dir: Path
) -> None:
    files = discover_files(["eod_prices"], raw_dir=sample_raw_dir)
    sf = files[0].source_file
    assert already_loaded(pg_conn, sf) is False
    run_load(["eod_prices"], raw_dir=sample_raw_dir, conn=pg_conn)
    assert already_loaded(pg_conn, sf) is True


def test_failed_file_isolated_and_audited(
    pg_conn: psycopg.Connection, tmp_path: Path
) -> None:
    # One good file + one malformed file (bad date) in the same dataset dir.
    ddir = tmp_path / "eod_prices"
    ddir.mkdir(parents=True)
    header = (
        "ticker,date,open,high,low,close,adj_close,volume,currency,source,ingested_at\n"
    )
    good = (
        header
        + "AAPL,2024-01-02,1,2,0,1,1,10,USD,yfinance,2026-06-04T09:59:00+00:00\n"
    )
    bad = (
        header
        + "AAPL,NOT-A-DATE,1,2,0,1,1,10,USD,yfinance,2026-06-04T09:59:00+00:00\n"
    )
    (ddir / "AAPL_202606040959.csv").write_text(good, encoding="utf-8")
    (ddir / "AAPL_202606041000.csv").write_text(bad, encoding="utf-8")

    result = run_load(["eod_prices"], raw_dir=tmp_path, conn=pg_conn)
    statuses = {r.raw_file.path.name: r.status for r in result.results}
    assert statuses["AAPL_202606040959.csv"] == "ok"
    assert statuses["AAPL_202606041000.csv"] == "failed"
    # Good file's rows are committed; failed file contributed nothing.
    assert _count(pg_conn, "eod_prices") == 1
    audit = {sf: (rows, status) for _, sf, rows, status in _audit_rows(pg_conn)}
    assert audit["eod_prices/AAPL_202606040959.csv"] == (1, "ok")
    assert audit["eod_prices/AAPL_202606041000.csv"][1] == "failed"


def test_financial_dirs_target_single_table(
    pg_conn: psycopg.Connection, sample_raw_dir: Path
) -> None:
    run_load(["balance_sheet", "income_statement", "cash_flow"],
             raw_dir=sample_raw_dir, conn=pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT statement, count(*) FROM raw_yfinance.financial_statements "
            "GROUP BY statement ORDER BY statement"
        )
        by_stmt = {r[0]: r[1] for r in cur.fetchall()}
    assert set(by_stmt) == {"balance_sheet", "income_statement", "cash_flow"}
    assert DATASET_TARGETS["balance_sheet"].table == "financial_statements"
