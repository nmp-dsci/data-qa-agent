"""Out-of-band S&P 500 constituent seed refresher (NOT in the pipeline run path).

Scrapes the Wikipedia "List of S&P 500 companies" table, normalizes tickers to Yahoo's
class-share convention, validates the shape, and overwrites the committed seed at
``dbt/seeds/sp500_constituents.csv``. This is the only component allowed to hit
Wikipedia; the extract pipeline and tests never do.

Usage::

    uv run python scripts/refresh_sp500_seed.py            # refresh the committed seed
    uv run python scripts/refresh_sp500_seed.py --dry-run  # print diff vs current, write nothing
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = REPO_ROOT / "dbt" / "seeds" / "sp500_constituents.csv"
WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

SEED_COLUMNS = [
    "ticker",
    "company_name",
    "gics_sector",
    "gics_sub_industry",
    "date_added",
    "cik",
    "retrieved_at",
]

# Wikipedia column -> seed column.
_COLUMN_MAP = {
    "Symbol": "ticker",
    "Security": "company_name",
    "GICS Sector": "gics_sector",
    "GICS Sub-Industry": "gics_sub_industry",
    "Date added": "date_added",
    "CIK": "cik",
}

_TICKER_RE = re.compile(r"^[A-Z]+(-[A-Z])?$")


def normalize_ticker(ticker: str) -> str:
    """Uppercase + convert Wikipedia class shares (``BRK.B``) to Yahoo's (``BRK-B``)."""
    return str(ticker).strip().upper().replace(".", "-")


def fetch_constituents() -> pd.DataFrame:
    """Read the first Wikipedia table and map/normalize to the seed schema."""
    tables = pd.read_html(WIKI_URL)
    if not tables:
        raise RuntimeError("Wikipedia returned no tables (layout drift?)")
    df = tables[0]
    missing = [c for c in _COLUMN_MAP if c not in df.columns]
    if missing:
        raise RuntimeError(
            f"Wikipedia table missing expected columns {missing}; got {list(df.columns)}"
        )
    out = df[list(_COLUMN_MAP)].rename(columns=_COLUMN_MAP).copy()
    out["ticker"] = out["ticker"].map(normalize_ticker)
    # CIK is a zero-padded 10-digit identifier; keep as string to preserve leading zeros.
    out["cik"] = out["cik"].apply(lambda v: str(int(v)).zfill(10) if pd.notna(v) else "")
    retrieved_at = datetime.now(UTC).isoformat()
    out["retrieved_at"] = retrieved_at
    out = out[SEED_COLUMNS].sort_values("ticker").reset_index(drop=True)
    return out


def validate(df: pd.DataFrame) -> None:
    """Guard against Wikipedia layout drift landing a bad list."""
    n = len(df)
    if not (490 <= n <= 515):
        raise RuntimeError(f"unexpected row count {n}; expected ~500-505 (layout drift?)")
    if df["ticker"].isna().any() or (df["ticker"].str.len() == 0).any():
        raise RuntimeError("found null/empty tickers")
    if df["ticker"].duplicated().any():
        dupes = sorted(df.loc[df["ticker"].duplicated(), "ticker"])
        raise RuntimeError(f"duplicate tickers: {dupes}")
    bad = sorted(t for t in df["ticker"] if not _TICKER_RE.match(t))
    if bad:
        raise RuntimeError(f"tickers failing ^[A-Z]+(-[A-Z])?$: {bad}")


def _print_diff(new: pd.DataFrame, seed_path: Path) -> None:
    if not seed_path.exists():
        print(f"(no existing seed at {seed_path}; would create {len(new)} rows)")
        return
    old = pd.read_csv(seed_path, dtype=str)
    old_t = set(old.get("ticker", pd.Series(dtype=str)))
    new_t = set(new["ticker"])
    added = sorted(new_t - old_t)
    removed = sorted(old_t - new_t)
    print(f"current seed: {len(old)} rows; new: {len(new)} rows")
    print(f"added ({len(added)}): {added}")
    print(f"removed ({len(removed)}): {removed}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh the committed S&P 500 seed.")
    parser.add_argument(
        "--dry-run", action="store_true", help="print diff vs current seed, write nothing"
    )
    parser.add_argument("--seed-path", type=Path, default=SEED_PATH)
    args = parser.parse_args(argv)

    try:
        df = fetch_constituents()
        validate(df)
    except Exception as exc:  # noqa: BLE001 - surface a clear abort to the operator
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        _print_diff(df, args.seed_path)
        print("dry-run: no file written")
        return 0

    args.seed_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.seed_path, index=False)
    print(f"wrote {len(df)} constituents -> {args.seed_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
