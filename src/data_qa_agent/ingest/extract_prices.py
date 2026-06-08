"""EOD prices + corporate actions (date-grained, incremental)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from .landing import load_id_to_iso
from .models import DatasetState
from .yf_client import YFClient

logger = logging.getLogger(__name__)

SOURCE = "yfinance"

EOD_COLUMNS = [
    "ticker",
    "date",
    "open",
    "high",
    "low",
    "close",
    "adj_close",
    "volume",
    "currency",
    "source",
    "ingested_at",
]

CORP_ACTION_COLUMNS = [
    "ticker",
    "date",
    "action_type",
    "value",
    "source",
    "ingested_at",
]


@dataclass
class PriceExtractResult:
    eod_prices: pd.DataFrame
    corporate_actions: pd.DataFrame
    new_last_date: str | None


def _history_start(state: DatasetState, years: int, today: date, force: bool) -> date:
    """Window start: ``last_date + 1`` for incremental, ``today - years`` for force/cold."""
    cold = today - timedelta(days=round(years * 365.25))
    if force or not state.last_date:
        return cold
    last = date.fromisoformat(state.last_date)
    return last + timedelta(days=1)


def extract_prices(
    client: YFClient,
    ticker: str,
    state: DatasetState,
    load_id: str,
    *,
    years: int = 10,
    today: date | None = None,
    force: bool = False,
    currency: str | None = None,
) -> PriceExtractResult:
    """Pull history for the incremental window and split into EOD + corporate actions.

    ``end`` is yfinance-exclusive, so we pass ``today + 1`` to include today.
    ``currency`` may be supplied by the caller (from the profile pull) to avoid a
    duplicate ``info`` network call; if ``None`` it is fetched best-effort.
    """
    today = today or date.today()
    start = _history_start(state, years, today, force)
    end = today + timedelta(days=1)

    ingested_at = load_id_to_iso(load_id)

    if start >= end:
        return PriceExtractResult(
            eod_prices=pd.DataFrame(columns=EOD_COLUMNS),
            corporate_actions=pd.DataFrame(columns=CORP_ACTION_COLUMNS),
            new_last_date=state.last_date,
        )

    hist = client.history(
        ticker,
        start=start.isoformat(),
        end=end.isoformat(),
        interval="1d",
        auto_adjust=False,
        actions=True,
    )

    if hist is None or hist.empty:
        return PriceExtractResult(
            eod_prices=pd.DataFrame(columns=EOD_COLUMNS),
            corporate_actions=pd.DataFrame(columns=CORP_ACTION_COLUMNS),
            new_last_date=state.last_date,
        )

    hist = hist.reset_index()
    # The date/datetime index column is named "Date" (daily) — normalize to a naive
    # exchange-local trading date.
    date_col = "Date" if "Date" in hist.columns else hist.columns[0]
    hist["_trading_date"] = pd.to_datetime(hist[date_col]).dt.tz_localize(None).dt.date

    # Guard: Yahoo's start filter can return a boundary/prior bar (e.g. it echoes the
    # last completed bar when "today" has no finished bar yet). For incremental pulls,
    # strictly drop anything at or before the watermark so re-runs are true no-ops.
    if not force and state.last_date:
        wm = date.fromisoformat(state.last_date)
        hist = hist[hist["_trading_date"] > wm]

    if hist.empty:
        return PriceExtractResult(
            eod_prices=pd.DataFrame(columns=EOD_COLUMNS),
            corporate_actions=pd.DataFrame(columns=CORP_ACTION_COLUMNS),
            new_last_date=state.last_date,
        )

    hist = hist.reset_index(drop=True)
    trading_date = hist["_trading_date"]

    if currency is None:
        currency = _safe_currency(client, ticker)

    eod = pd.DataFrame(
        {
            "ticker": ticker,
            "date": trading_date,
            "open": _col(hist, "Open"),
            "high": _col(hist, "High"),
            "low": _col(hist, "Low"),
            "close": _col(hist, "Close"),
            "adj_close": _col(hist, "Adj Close"),
            "volume": _col(hist, "Volume"),
            "currency": currency,
            "source": SOURCE,
            "ingested_at": ingested_at,
        }
    )
    eod = eod.dropna(subset=["open", "high", "low", "close"], how="all")
    eod = eod.drop_duplicates(subset=["ticker", "date"]).sort_values(["ticker", "date"])
    eod = eod[EOD_COLUMNS].reset_index(drop=True)

    actions = _build_corporate_actions(hist, trading_date, ticker, ingested_at)

    new_last = state.last_date
    if not eod.empty:
        fmax = str(eod["date"].max())
        if new_last is None or fmax > new_last:
            new_last = fmax

    return PriceExtractResult(eod_prices=eod, corporate_actions=actions, new_last_date=new_last)


def _build_corporate_actions(
    hist: pd.DataFrame, trading_date: pd.Series, ticker: str, ingested_at: str
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    div = _col(hist, "Dividends")
    if div is not None:
        d = pd.DataFrame({"date": trading_date, "value": div})
        d = d[d["value"].fillna(0) != 0]
        if not d.empty:
            d["action_type"] = "dividend"
            frames.append(d)
    spl = _col(hist, "Stock Splits")
    if spl is not None:
        s = pd.DataFrame({"date": trading_date, "value": spl})
        s = s[s["value"].fillna(0) != 0]
        if not s.empty:
            s["action_type"] = "split"
            frames.append(s)
    if not frames:
        return pd.DataFrame(columns=CORP_ACTION_COLUMNS)
    out = pd.concat(frames, ignore_index=True)
    out["ticker"] = ticker
    out["source"] = SOURCE
    out["ingested_at"] = ingested_at
    out = out.sort_values(["ticker", "date", "action_type"])
    return out[CORP_ACTION_COLUMNS].reset_index(drop=True)


def _col(df: pd.DataFrame, name: str) -> pd.Series | None:
    return df[name] if name in df.columns else None


def _safe_currency(client: YFClient, ticker: str) -> str | None:
    try:
        info = client.info(ticker)
    except Exception:  # noqa: BLE001 - currency is best-effort metadata
        return None
    cur = info.get("currency")
    return str(cur) if cur else None
