"""Resolve the ticker universe: explicit ``--tickers`` or the committed S&P 500 seed."""

from __future__ import annotations

import csv
from pathlib import Path

from data_qa_agent.config import REPO_ROOT

SEED_PATH = REPO_ROOT / "dbt" / "seeds" / "sp500_constituents.csv"


def normalize_ticker(ticker: str) -> str:
    """Uppercase and convert class-share notation to Yahoo's (``BRK.B`` -> ``BRK-B``)."""
    return ticker.strip().upper().replace(".", "-")


def load_sp500_seed(seed_path: Path | None = None) -> list[str]:
    """Read the committed S&P 500 seed and return normalized tickers (sorted, unique)."""
    path = seed_path or SEED_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"S&P 500 seed not found at {path}; run scripts/refresh_sp500_seed.py"
        )
    tickers: list[str] = []
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or "ticker" not in reader.fieldnames:
            raise ValueError(f"seed {path} missing 'ticker' column")
        for row in reader:
            raw = row.get("ticker", "")
            if raw and raw.strip():
                tickers.append(normalize_ticker(raw))
    return sorted(set(tickers))


def resolve_tickers(
    explicit: list[str] | None,
    universe: str | None,
    seed_path: Path | None = None,
) -> list[str]:
    """Resolve the universe. ``explicit`` wins; else ``universe='sp500'`` reads the seed."""
    if explicit:
        return sorted({normalize_ticker(t) for t in explicit if t.strip()})
    if universe == "sp500":
        return load_sp500_seed(seed_path)
    if universe:
        raise ValueError(f"unknown universe: {universe!r} (expected 'sp500')")
    raise ValueError("no tickers: pass --tickers or --universe sp500")
