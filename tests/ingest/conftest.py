"""Shared fixtures: a fake YFClient (no network) and a temp raw dir."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from data_qa_agent.ingest.yf_client import YFClient


class FakeYFClient(YFClient):
    """In-memory stand-in for YFClient. Records calls; serves canned DataFrames."""

    def __init__(
        self,
        *,
        history_df: pd.DataFrame | None = None,
        financials: dict[tuple[str, str], pd.DataFrame] | None = None,
        info: dict[str, object] | None = None,
    ) -> None:
        super().__init__()
        self._history_df = history_df
        self._financials = financials or {}
        self._info = info or {}
        self.history_calls: list[dict[str, object]] = []
        self.info_calls = 0

    def history(self, ticker: str, *, start=None, end=None, **kwargs) -> pd.DataFrame:  # type: ignore[override]
        self.history_calls.append({"ticker": ticker, "start": start, "end": end})
        df = self._history_df if self._history_df is not None else _empty_history()
        # Emulate yfinance start(inclusive)/end(exclusive) date filtering.
        if not df.empty and (start or end):
            idx = pd.to_datetime(df.index).tz_localize(None)
            mask = pd.Series(True, index=df.index)
            if start:
                mask &= idx >= pd.Timestamp(start)
            if end:
                mask &= idx < pd.Timestamp(end)
            df = df[mask.to_numpy()]
        return df.copy()

    def balance_sheet(self, ticker: str, *, freq: str) -> pd.DataFrame:  # type: ignore[override]
        return self._financials.get(("balance_sheet", freq), pd.DataFrame()).copy()

    def income_stmt(self, ticker: str, *, freq: str) -> pd.DataFrame:  # type: ignore[override]
        return self._financials.get(("income_statement", freq), pd.DataFrame()).copy()

    def cash_flow(self, ticker: str, *, freq: str) -> pd.DataFrame:  # type: ignore[override]
        return self._financials.get(("cash_flow", freq), pd.DataFrame()).copy()

    def info(self, ticker: str):  # type: ignore[override]
        self.info_calls += 1
        return dict(self._info)


def _empty_history() -> pd.DataFrame:
    idx = pd.DatetimeIndex([], name="Date")
    return pd.DataFrame(
        {c: [] for c in ["Open", "High", "Low", "Close", "Adj Close", "Volume",
                         "Dividends", "Stock Splits"]},
        index=idx,
    )


def make_history(
    start: date,
    n_days: int,
    *,
    tz: str = "America/New_York",
    dividends_on: dict[int, float] | None = None,
    splits_on: dict[int, float] | None = None,
) -> pd.DataFrame:
    """Build a tz-aware daily history frame like yfinance returns."""
    dates = [start + timedelta(days=i) for i in range(n_days)]
    idx = pd.DatetimeIndex(pd.to_datetime(dates), name="Date").tz_localize(tz)
    dividends = [0.0] * n_days
    splits = [0.0] * n_days
    for i, v in (dividends_on or {}).items():
        dividends[i] = v
    for i, v in (splits_on or {}).items():
        splits[i] = v
    return pd.DataFrame(
        {
            "Open": [10.0 + i for i in range(n_days)],
            "High": [11.0 + i for i in range(n_days)],
            "Low": [9.0 + i for i in range(n_days)],
            "Close": [10.5 + i for i in range(n_days)],
            "Adj Close": [10.4 + i for i in range(n_days)],
            "Volume": [1000 + i for i in range(n_days)],
            "Dividends": dividends,
            "Stock Splits": splits,
        },
        index=idx,
    )


def make_statement(periods: list[str], line_items: dict[str, list[float]]) -> pd.DataFrame:
    """Wide statement: rows=line items, columns=period-end dates (like yfinance)."""
    cols = pd.to_datetime(periods)
    data = {item: vals for item, vals in line_items.items()}
    return pd.DataFrame(data, index=cols).T


@pytest.fixture
def raw_dir(tmp_path):
    d = tmp_path / "raw"
    d.mkdir()
    return d


@pytest.fixture
def aapl_info():
    return {
        "longName": "Apple Inc.",
        "shortName": "Apple",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "currency": "USD",
        "exchange": "NMS",
        "country": "United States",
    }
