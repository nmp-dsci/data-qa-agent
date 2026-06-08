from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from data_qa_agent.ingest import landing
from data_qa_agent.ingest.models import ALL_DATASETS, Dataset
from data_qa_agent.ingest.run_extract import _parse_datasets, run_extract

from .conftest import FakeYFClient, make_history, make_statement


def _full_client():
    hist = make_history(date(2026, 1, 1), 5, dividends_on={1: 0.25}, splits_on={3: 2.0})
    annual = make_statement(
        ["2025-09-30", "2024-09-30"],
        {"TotalAssets": [364000.0, 352000.0], "TotalDebt": [50.0, np.nan]},
    )
    quarterly = make_statement(["2026-03-31"], {"TotalAssets": [370000.0]})
    fin = {
        ("balance_sheet", "yearly"): annual,
        ("balance_sheet", "quarterly"): quarterly,
        ("income_statement", "yearly"): make_statement(
            ["2025-09-30"], {"TotalRevenue": [400000.0]}
        ),
        ("income_statement", "quarterly"): make_statement(
            ["2026-03-31"], {"TotalRevenue": [90000.0]}
        ),
        ("cash_flow", "yearly"): make_statement(
            ["2025-09-30"], {"FreeCashFlow": [100000.0]}
        ),
        ("cash_flow", "quarterly"): make_statement(
            ["2026-03-31"], {"FreeCashFlow": [25000.0]}
        ),
    }
    info = {
        "longName": "Apple Inc.",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "currency": "USD",
        "exchange": "NMS",
        "country": "United States",
    }
    return FakeYFClient(history_df=hist, financials=fin, info=info)


def test_parse_datasets_default_all():
    assert _parse_datasets(None) == list(ALL_DATASETS)
    assert _parse_datasets("eod_prices,cash_flow") == [Dataset.EOD_PRICES, Dataset.CASH_FLOW]


def test_full_run_writes_all_datasets(raw_dir):
    summary = run_extract(
        ["AAPL"],
        list(ALL_DATASETS),
        years=10,
        client=_full_client(),
        raw_dir=raw_dir,
        today=date(2026, 1, 6),
    )
    counts = summary.counts
    assert counts["failed"] == 0
    # every dataset wrote exactly one file
    for ds in ALL_DATASETS:
        files = list((raw_dir / ds.value).glob("AAPL_*.csv"))
        assert len(files) == 1, ds.value

    # state recorded last date + period-ends
    state = landing.load_state(raw_dir)
    assert state["AAPL"]["eod_prices"].last_date == "2026-01-05"
    assert "annual|2025-09-30" in state["AAPL"]["balance_sheet"].period_ends
    assert "quarterly|2026-03-31" in state["AAPL"]["balance_sheet"].period_ends


def test_rerun_is_noop(raw_dir):
    today = date(2026, 1, 6)
    run_extract(["AAPL"], list(ALL_DATASETS), client=_full_client(), raw_dir=raw_dir, today=today)
    files_before = sorted(p.name for p in raw_dir.rglob("AAPL_*.csv"))

    summary2 = run_extract(
        ["AAPL"], list(ALL_DATASETS), client=_full_client(), raw_dir=raw_dir, today=today
    )
    files_after = sorted(p.name for p in raw_dir.rglob("AAPL_*.csv"))

    assert files_before == files_after  # no new files
    assert summary2.counts["ok"] == 0
    assert summary2.counts["noop"] == len(ALL_DATASETS)


def test_force_writes_new_files(raw_dir):
    today = date(2026, 1, 6)
    run_extract(
        ["AAPL"], list(ALL_DATASETS), client=_full_client(), raw_dir=raw_dir,
        today=today, load_id="202601061200",
    )
    # force re-pull at a later minute -> new timestamped files coexist
    summary = run_extract(
        ["AAPL"],
        list(ALL_DATASETS),
        client=_full_client(),
        raw_dir=raw_dir,
        today=today,
        force=True,
        load_id="202601061205",
    )
    # eod prices should have a fresh full-window file (>=1 new)
    assert summary.counts["ok"] >= 1
    # original files still present (immutable)
    assert len(list((raw_dir / "eod_prices").glob("AAPL_*.csv"))) >= 1


def test_force_dataset_targets_one(raw_dir):
    today = date(2026, 1, 6)
    run_extract(
        ["AAPL"], list(ALL_DATASETS), client=_full_client(), raw_dir=raw_dir,
        today=today, load_id="202601061200",
    )
    summary = run_extract(
        ["AAPL"],
        list(ALL_DATASETS),
        client=_full_client(),
        raw_dir=raw_dir,
        today=today,
        force_datasets={Dataset.EOD_PRICES},
        load_id="202601061205",
    )
    # only eod_prices re-pulled; the rest are no-ops
    ok = {r.dataset for r in summary.results if r.status == "ok"}
    assert Dataset.EOD_PRICES in ok
    assert Dataset.BALANCE_SHEET not in ok


def test_dry_run_writes_nothing(raw_dir):
    summary = run_extract(
        ["AAPL"],
        list(ALL_DATASETS),
        client=_full_client(),
        raw_dir=raw_dir,
        today=date(2026, 1, 6),
        dry_run=True,
    )
    assert list(raw_dir.rglob("AAPL_*.csv")) == []
    assert not (raw_dir / landing.STATE_FILENAME).exists()
    assert summary.counts["ok"] >= 1  # would-write reported


def test_partial_failure_isolation(raw_dir):
    class BoomClient(FakeYFClient):
        def history(self, ticker, **kwargs):
            if ticker == "BOOM":
                raise RuntimeError("kaboom")
            return super().history(ticker, **kwargs)

    good = _full_client()
    boom = BoomClient(
        history_df=good._history_df, financials=good._financials, info=good._info
    )
    summary = run_extract(
        ["BOOM", "AAPL"],
        [Dataset.EOD_PRICES],
        client=boom,
        raw_dir=raw_dir,
        today=date(2026, 1, 6),
    )
    statuses = {(r.ticker, r.status) for r in summary.results}
    assert ("BOOM", "failed") in statuses
    assert ("AAPL", "ok") in statuses
    assert len(list((raw_dir / "eod_prices").glob("AAPL_*.csv"))) == 1


def test_eod_file_content_contract(raw_dir):
    run_extract(
        ["AAPL"], [Dataset.EOD_PRICES], client=_full_client(), raw_dir=raw_dir,
        today=date(2026, 1, 6),
    )
    f = next((raw_dir / "eod_prices").glob("AAPL_*.csv"))
    df = pd.read_csv(f)
    assert list(df.columns) == [
        "ticker", "date", "open", "high", "low", "close",
        "adj_close", "volume", "currency", "source", "ingested_at",
    ]
    assert not df.duplicated(subset=["ticker", "date"]).any()
