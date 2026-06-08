from __future__ import annotations

from datetime import date

import pandas as pd

from data_qa_agent.ingest.extract_prices import CORP_ACTION_COLUMNS, EOD_COLUMNS, extract_prices
from data_qa_agent.ingest.models import DatasetState

from .conftest import FakeYFClient, make_history

LOAD_ID = "202606041415"


def test_eod_columns_and_sort():
    hist = make_history(date(2026, 1, 1), 5)
    client = FakeYFClient(history_df=hist, info={"currency": "USD"})
    res = extract_prices(
        client, "AAPL", DatasetState(), LOAD_ID, years=10, today=date(2026, 1, 6), currency="USD"
    )
    eod = res.eod_prices
    assert list(eod.columns) == EOD_COLUMNS
    assert eod["ticker"].eq("AAPL").all()
    assert eod["source"].eq("yfinance").all()
    assert eod["currency"].eq("USD").all()
    assert eod["date"].is_monotonic_increasing
    assert eod["date"].is_unique
    # naive trading dates (datetime.date, not timestamp)
    assert isinstance(eod["date"].iloc[0], date)
    assert res.new_last_date == "2026-01-05"  # today=01-06 exclusive end -> last is 01-05


def test_incremental_start_is_last_plus_one():
    hist = make_history(date(2026, 1, 1), 10)
    client = FakeYFClient(history_df=hist, info={"currency": "USD"})
    state = DatasetState(last_date="2026-01-04")
    res = extract_prices(
        client, "AAPL", state, LOAD_ID, today=date(2026, 1, 10), currency="USD"
    )
    # window starts 2026-01-05; only rows >= that survive
    assert res.eod_prices["date"].min() == date(2026, 1, 5)
    call = client.history_calls[-1]
    assert call["start"] == "2026-01-05"
    assert call["end"] == "2026-01-11"  # today + 1, exclusive


def test_force_repulls_full_window():
    hist = make_history(date(2026, 1, 1), 10)
    client = FakeYFClient(history_df=hist, info={"currency": "USD"})
    state = DatasetState(last_date="2026-01-08")
    res = extract_prices(
        client, "AAPL", state, LOAD_ID, years=10, today=date(2026, 1, 10),
        force=True, currency="USD",
    )
    # force ignores the watermark -> start is today-10y, all rows returned
    assert len(res.eod_prices) == 10


def test_corporate_actions_derivation():
    hist = make_history(
        date(2026, 1, 1), 6, dividends_on={2: 0.25}, splits_on={4: 4.0}
    )
    client = FakeYFClient(history_df=hist, info={"currency": "USD"})
    res = extract_prices(
        client, "AAPL", DatasetState(), LOAD_ID, today=date(2026, 1, 7), currency="USD"
    )
    ca = res.corporate_actions
    assert list(ca.columns) == CORP_ACTION_COLUMNS
    assert set(ca["action_type"]) == {"dividend", "split"}
    div = ca[ca["action_type"] == "dividend"]
    assert div["value"].iloc[0] == 0.25
    assert div["date"].iloc[0] == date(2026, 1, 3)
    split = ca[ca["action_type"] == "split"]
    assert split["value"].iloc[0] == 4.0


def test_boundary_echo_filtered_to_noop():
    # Simulate Yahoo echoing the already-fetched watermark bar on an incremental pull
    # (start filter returns the last completed bar). It must be dropped -> empty delta.
    class EchoClient(FakeYFClient):
        def history(self, ticker, **kwargs):
            # ignore start/end filtering — echo the watermark bar unconditionally
            return self._history_df.copy()

    hist = make_history(date(2026, 1, 5), 1)  # only 2026-01-05, == watermark
    client = EchoClient(history_df=hist, info={"currency": "USD"})
    state = DatasetState(last_date="2026-01-05")
    res = extract_prices(
        client, "AAPL", state, LOAD_ID, today=date(2026, 1, 6), currency="USD"
    )
    assert res.eod_prices.empty
    assert res.corporate_actions.empty
    assert res.new_last_date == "2026-01-05"


def test_empty_history_yields_empty_frames():
    client = FakeYFClient(history_df=pd.DataFrame(), info={"currency": "USD"})
    res = extract_prices(
        client, "AAPL", DatasetState(last_date="2026-01-01"),
        LOAD_ID, today=date(2026, 1, 2), currency="USD",
    )
    assert res.eod_prices.empty
    assert res.corporate_actions.empty
    assert res.new_last_date == "2026-01-01"
