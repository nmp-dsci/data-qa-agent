#!/usr/bin/env python3
"""Build small, committed sample CSVs from the full NSW datasets.

The full sources (data/nswgov_df.csv ~516 MB, data/rentboard_df.csv ~63 MB) are
too big to commit or bake into images, so they stay local-only (gitignored) and
are loaded on demand via `make pipeline`. This script distills them into small
samples under data/samples/ that keep the SAME headers/columns (so the pipeline
code is identical for sample and full) and preserve what the growth marts need:
suburbs/postcodes present in BOTH datasets, with rows near both ends of the
growth window (2016 and 2024).

Usage:  python3 scripts/make_samples.py
"""

from __future__ import annotations

import collections
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SALES_SRC = ROOT / "data" / "nswgov_df.csv"
RENT_SRC = ROOT / "data" / "rentboard_df.csv"
OUT_DIR = ROOT / "data" / "samples"

WINDOW = (2016, 2024)
# Keep only rows near both ends of the window so the sample stays small yet still
# has the endpoints the growth calc needs. (start ≤ 2017, end ≥ 2023.)
SAMPLE_YEARS = {2016, 2017, 2023, 2024}
N_POSTCODES = 25  # how many overlapping postcodes to keep
N_SUBURBS = 24  # keep the best-covered suburbs (volume at both ends)
# dbt's min_sales_year/min_rent_year vars require >=15 rows in a bucket to trust
# it (see dbt_project.yml) — cap comfortably above that so the sample's blended
# 'ALL' property_type rows (house+unit combined) clear the threshold.
PER_SUBURB_YEAR = 20  # cap sales rows per (suburb, year)
PER_POSTCODE_YEAR = 30  # cap rent rows per (postcode, year)
MIN_BUCKET_VOLUME = 20  # only pick suburbs/postcodes with at least this many real rows


def _is_start(year: int) -> bool:
    return year <= WINDOW[0] + 1


def _is_end(year: int) -> bool:
    return year >= WINDOW[1] - 1


def _sales_year(row: dict) -> int | None:
    raw = (row.get("contract_dt") or "").split(".", 1)[0][:8]
    if len(raw) == 8 and raw.isdigit():
        year = int(raw[:4])
        if 1900 <= year <= 2100:
            return year
    return None


def _rent_year(row: dict) -> int | None:
    raw = (row.get("lodgement_dt") or "")[:4]
    return int(raw) if raw.isdigit() else None


def rent_postcodes_in_window() -> list[str]:
    """Postcodes with rent rows at both ends of the window, by volume."""
    ends = collections.defaultdict(set)
    volume: collections.Counter[str] = collections.Counter()
    with RENT_SRC.open(newline="") as f:
        for row in csv.DictReader(f):
            y = _rent_year(row)
            pc = row.get("postcode")
            if not pc or y is None:
                continue
            volume[pc] += 1
            if y <= WINDOW[0] + 1:
                ends[pc].add("start")
            elif y >= WINDOW[1] - 1:
                ends[pc].add("end")
    both = [pc for pc, e in ends.items() if {"start", "end"} <= e]
    both.sort(key=lambda pc: volume[pc], reverse=True)
    return both[:N_POSTCODES]


def sample_rent(postcodes: set[str]) -> None:
    caps: collections.Counter[tuple[str, int]] = collections.Counter()
    with (
        RENT_SRC.open(newline="") as f,
        (OUT_DIR / "nsw_rent_sample.csv").open("w", newline="") as out,
    ):
        reader = csv.DictReader(f)
        writer = csv.DictWriter(out, fieldnames=reader.fieldnames or [])
        writer.writeheader()
        for row in reader:
            pc = row.get("postcode")
            y = _rent_year(row)
            if pc not in postcodes or y is None or y not in SAMPLE_YEARS:
                continue
            key = (pc, y)
            if caps[key] >= PER_POSTCODE_YEAR:
                continue
            caps[key] += 1
            writer.writerow(row)


def _residential_sale(row: dict, postcodes: set[str]) -> tuple[str, int] | None:
    """Return (suburb, year) for a usable residential sale row, else None."""
    pc = (row.get("postcode") or "").split(".", 1)[0]
    y = _sales_year(row)
    if pc not in postcodes or y is None or y not in SAMPLE_YEARS:
        return None
    if row.get("prop_nature") != "R" or not row.get("locality"):
        return None
    try:
        if float(row.get("sale_price") or 0) <= 0:
            return None
    except ValueError:
        return None
    return row["locality"], y


def pick_suburbs(postcodes: set[str]) -> set[str]:
    """Suburbs with sales volume at BOTH ends of the window (best-covered first)."""
    start: collections.Counter[str] = collections.Counter()
    end: collections.Counter[str] = collections.Counter()
    with SALES_SRC.open(newline="") as f:
        for row in csv.DictReader(f):
            hit = _residential_sale(row, postcodes)
            if not hit:
                continue
            suburb, year = hit
            (start if _is_start(year) else end)[suburb] += 1
    both = [s for s in start if end[s] >= MIN_BUCKET_VOLUME and start[s] >= MIN_BUCKET_VOLUME]
    both.sort(key=lambda s: min(start[s], end[s]), reverse=True)
    return set(both[:N_SUBURBS])


def sample_sales(postcodes: set[str], suburbs: set[str]) -> None:
    caps: collections.Counter[tuple[str, int]] = collections.Counter()
    with (
        SALES_SRC.open(newline="") as f,
        (OUT_DIR / "nsw_sales_sample.csv").open("w", newline="") as out,
    ):
        reader = csv.DictReader(f)
        writer = csv.DictWriter(out, fieldnames=reader.fieldnames or [])
        writer.writeheader()
        for row in reader:
            hit = _residential_sale(row, postcodes)
            if not hit or hit[0] not in suburbs:
                continue
            if caps[hit] >= PER_SUBURB_YEAR:
                continue
            caps[hit] += 1
            writer.writerow(row)


def main() -> None:
    for src in (SALES_SRC, RENT_SRC):
        if not src.exists():
            raise SystemExit(f"Missing source {src} — place the full CSVs in data/ first.")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Scanning rent for overlapping postcodes in the growth window…")
    postcodes = set(rent_postcodes_in_window())
    print(f"  chose {len(postcodes)} postcodes")

    print("Scanning sales for well-covered suburbs (volume at both ends)…")
    suburbs = pick_suburbs(postcodes)
    print(f"  chose {len(suburbs)} suburbs")

    print("Writing rent sample…")
    sample_rent(postcodes)
    print("Writing sales sample… (sales postcodes carry a trailing '.0'; matched on int part)")
    sample_sales(postcodes, suburbs)

    for name in ("nsw_sales_sample.csv", "nsw_rent_sample.csv"):
        p = OUT_DIR / name
        rows = sum(1 for _ in p.open()) - 1
        print(f"  {name}: {rows} rows")


if __name__ == "__main__":
    main()
