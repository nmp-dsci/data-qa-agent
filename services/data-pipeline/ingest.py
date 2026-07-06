"""dlt ingestion: stream the NSW CSVs into the Postgres `raw` schema.

Chosen per Decision I (dlt for CSV -> Postgres). Two sources are supported and
selected by PIPELINE_SOURCE:
  sample -> data/samples/*.csv  (small, committed; the default — fast + CI-safe)
  full   -> data/nswgov_df.csv + data/rentboard_df.csv (the real 516MB/63MB files)

The messy columns the dbt layer parses (dates as YYYYMMDD floats, numeric-looking
strings) are pinned to `text` here so dbt — not dlt's type inference — owns the
casting/cleaning.
"""

from __future__ import annotations

import csv
import os
from collections.abc import Iterator
from pathlib import Path

import dlt

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
SOURCE = os.environ.get("PIPELINE_SOURCE", "sample")

FILES: dict[str, dict[str, Path]] = {
    "sample": {
        "property_sales": DATA_DIR / "samples" / "nsw_sales_sample.csv",
        "property_rent": DATA_DIR / "samples" / "nsw_rent_sample.csv",
    },
    "full": {
        "property_sales": DATA_DIR / "nswgov_df.csv",
        "property_rent": DATA_DIR / "rentboard_df.csv",
    },
}

# Keep dbt in control of typing for the fields it parses.
SALES_TEXT = ["contract_dt", "settle_dt", "sale_price", "postcode", "area_sqm"]
RENT_TEXT = ["lodgement_dt", "postcode", "weekly_rent", "bedrooms"]


def _rows(path: Path) -> Iterator[dict]:
    with path.open(newline="") as f:
        yield from csv.DictReader(f)


def _text_hints(columns: list[str]) -> dict[str, dict[str, str]]:
    return {c: {"data_type": "text"} for c in columns}


def main() -> None:
    files = FILES.get(SOURCE)
    if files is None:
        raise SystemExit(f"PIPELINE_SOURCE must be one of {list(FILES)}, got {SOURCE!r}")
    for path in files.values():
        if not path.exists():
            raise SystemExit(f"Missing input {path} (PIPELINE_SOURCE={SOURCE})")

    # dlt reads the destination from DESTINATION__POSTGRES__CREDENTIALS (set by run.py).
    pipeline = dlt.pipeline(
        pipeline_name="nsw_property",
        destination="postgres",
        dataset_name="raw",
        progress="log",
    )

    sales = dlt.resource(
        _rows(files["property_sales"]),
        name="property_sales",
        write_disposition="replace",
        columns=_text_hints(SALES_TEXT),
    )
    rent = dlt.resource(
        _rows(files["property_rent"]),
        name="property_rent",
        write_disposition="replace",
        columns=_text_hints(RENT_TEXT),
    )

    print(
        f"==> dlt ingest ({SOURCE}): "
        f"{files['property_sales'].name}, {files['property_rent'].name}"
    )
    info = pipeline.run([sales, rent])
    print(info)


if __name__ == "__main__":
    main()
