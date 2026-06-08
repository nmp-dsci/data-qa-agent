"""Company profile snapshot from ``Ticker.info`` (subset of stable keys)."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from .landing import load_id_to_iso
from .yf_client import YFClient

logger = logging.getLogger(__name__)

PROFILE_COLUMNS = [
    "ticker",
    "company_name",
    "sector",
    "industry",
    "currency",
    "exchange",
    "country",
    "ingested_at",
]


def extract_profile(
    client: YFClient,
    ticker: str,
    load_id: str,
    *,
    info: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, str | None]:
    """Build a one-row profile snapshot. Returns ``(df, currency)``.

    ``currency`` is returned so the caller can reuse it for the price extract without a
    second ``info`` call. If ``info`` is passed in it is reused, else fetched.
    """
    if info is None:
        info = client.info(ticker)
    ingested_at = load_id_to_iso(load_id)
    company_name = info.get("longName") or info.get("shortName")
    currency = info.get("currency")
    row = {
        "ticker": ticker,
        "company_name": company_name,
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "currency": currency,
        "exchange": info.get("exchange"),
        "country": info.get("country"),
        "ingested_at": ingested_at,
    }
    df = pd.DataFrame([row])[PROFILE_COLUMNS]
    return df, (str(currency) if currency else None)
