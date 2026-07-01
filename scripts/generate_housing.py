#!/usr/bin/env python3
"""Generate a realistic sample housing dataset for the data-qa-agent demo.

Usage: python scripts/generate_housing.py [n_rows]
Writes data/incoming/housing.csv
"""

from __future__ import annotations

import csv
import random
import sys
from datetime import date, timedelta
from pathlib import Path

SUBURBS = [
    ("Fitzroy", 1_150_000),
    ("Carlton", 1_050_000),
    ("Brunswick", 980_000),
    ("Richmond", 1_120_000),
    ("St Kilda", 1_010_000),
    ("Footscray", 820_000),
    ("Coburg", 890_000),
    ("Preston", 810_000),
    ("Northcote", 1_180_000),
    ("Yarraville", 950_000),
]
PROPERTY_TYPES = ["House", "Townhouse", "Apartment", "Unit"]


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    rng = random.Random(42)
    out = Path(__file__).resolve().parent.parent / "data" / "incoming" / "housing.csv"
    out.parent.mkdir(parents=True, exist_ok=True)

    start = date(2023, 1, 1)
    rows = []
    for i in range(1, n + 1):
        suburb, base = rng.choice(SUBURBS)
        ptype = rng.choices(PROPERTY_TYPES, weights=[5, 3, 4, 2])[0]
        beds = rng.choice([1, 2, 2, 3, 3, 4, 5])
        baths = min(beds, rng.choice([1, 1, 2, 2, 3]))
        cars = rng.choice([0, 1, 1, 2, 2, 3])
        land = rng.randint(90, 720) if ptype in ("House", "Townhouse") else rng.randint(0, 120)
        type_factor = {"House": 1.15, "Townhouse": 1.0, "Apartment": 0.72, "Unit": 0.8}[ptype]
        price = int(
            base * type_factor * (0.75 + 0.16 * beds + 0.05 * baths) * rng.uniform(0.82, 1.22)
        )
        price = round(price, -3)
        sale_date = start + timedelta(days=rng.randint(0, 1000))
        year_built = rng.randint(1900, 2023)
        rows.append(
            {
                "id": i,
                "suburb": suburb,
                "property_type": ptype,
                "price": price,
                "bedrooms": beds,
                "bathrooms": baths,
                "car_spaces": cars,
                "land_size_sqm": land,
                "year_built": year_built,
                "sale_date": sale_date.isoformat(),
            }
        )

    fields = list(rows[0].keys())
    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows -> {out}")


if __name__ == "__main__":
    main()
