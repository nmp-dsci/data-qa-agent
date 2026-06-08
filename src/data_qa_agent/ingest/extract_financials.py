"""Balance sheet / income statement / cash flow -> tidy long delta (period-grained)."""

from __future__ import annotations

import logging

import pandas as pd

from .landing import load_id_to_iso, period_key
from .models import Dataset, DatasetState
from .yf_client import YFClient

logger = logging.getLogger(__name__)

SOURCE = "yfinance"

FINANCIALS_COLUMNS = [
    "ticker",
    "statement",
    "freq",
    "period_end",
    "line_item",
    "value",
    "currency",
    "source",
    "ingested_at",
]

# dataset -> (statement label written to CSV, YFClient method name)
_STATEMENT_BY_DATASET: dict[Dataset, tuple[str, str]] = {
    Dataset.BALANCE_SHEET: ("balance_sheet", "balance_sheet"),
    Dataset.INCOME_STATEMENT: ("income_statement", "income_stmt"),
    Dataset.CASH_FLOW: ("cash_flow", "cash_flow"),
}

# yfinance freq value -> our canonical freq label.
_FREQ_MAP = {"yearly": "annual", "quarterly": "quarterly"}


def extract_financials(
    client: YFClient,
    ticker: str,
    dataset: Dataset,
    state: DatasetState,
    load_id: str,
    *,
    force: bool = False,
    currency: str | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Pull annual + quarterly statements, melt to long, keep only unseen periods.

    Returns ``(delta_df, new_period_keys)``. ``new_period_keys`` is the full set of
    ``freq|period_end`` keys present in the delta (to merge into the watermark).
    """
    statement, method_name = _STATEMENT_BY_DATASET[dataset]
    ingested_at = load_id_to_iso(load_id)
    known = set() if force else set(state.period_ends)

    frames: list[pd.DataFrame] = []
    for yf_freq, our_freq in _FREQ_MAP.items():
        method = getattr(client, method_name)
        wide = method(ticker, freq=yf_freq)
        long_df = _melt_wide(wide, ticker, statement, our_freq, currency, ingested_at)
        if not long_df.empty:
            frames.append(long_df)

    if not frames:
        return pd.DataFrame(columns=FINANCIALS_COLUMNS), []

    allrows = pd.concat(frames, ignore_index=True)

    # Drop already-known (freq, period_end) periods.
    allrows["_pk"] = [
        period_key(f, p) for f, p in zip(allrows["freq"], allrows["period_end"], strict=False)
    ]
    delta = allrows[~allrows["_pk"].isin(known)].copy()
    new_keys = sorted(set(delta["_pk"])) if not delta.empty else []
    delta = delta.drop(columns=["_pk"])
    delta = delta.sort_values(["ticker", "freq", "period_end", "line_item"])
    delta = delta[FINANCIALS_COLUMNS].reset_index(drop=True)
    return delta, new_keys


def _melt_wide(
    wide: pd.DataFrame | None,
    ticker: str,
    statement: str,
    freq: str,
    currency: str | None,
    ingested_at: str,
) -> pd.DataFrame:
    """Reshape a wide statement (rows=line items, cols=period-end dates) to long."""
    if wide is None or not isinstance(wide, pd.DataFrame) or wide.empty:
        return pd.DataFrame(columns=FINANCIALS_COLUMNS)
    w = wide.copy()
    # Normalize period-end columns to ISO date strings up front. melt mishandles
    # datetime/Timestamp column labels (and duplicate Timestamps), so stringify first.
    period_cols = [
        str(pd.Timestamp(c).tz_localize(None).date())
        if not isinstance(c, str)
        else c
        for c in w.columns
    ]
    w.columns = pd.Index(period_cols)
    w.index = pd.Index([str(i) for i in w.index], name="line_item")
    long_df = w.reset_index().melt(
        id_vars="line_item", var_name="period_end", value_name="value"
    )
    # Coerce value numeric; drop all-NaN values.
    long_df["value"] = pd.to_numeric(long_df["value"], errors="coerce")
    long_df = long_df.dropna(subset=["value"])
    if long_df.empty:
        return pd.DataFrame(columns=FINANCIALS_COLUMNS)
    long_df["period_end"] = long_df["period_end"].astype(str)
    long_df["ticker"] = ticker
    long_df["statement"] = statement
    long_df["freq"] = freq
    long_df["currency"] = currency
    long_df["source"] = SOURCE
    long_df["ingested_at"] = ingested_at
    return long_df[FINANCIALS_COLUMNS]
