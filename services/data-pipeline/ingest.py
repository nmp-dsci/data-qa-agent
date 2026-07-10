"""dlt ingestion: stream the NSW CSVs into the Postgres `raw` schema.

Chosen per Decision I (dlt for CSV -> Postgres). Two sources are supported and
selected by PIPELINE_SOURCE:
  sample -> data/samples/*.csv  (small, committed; the default — fast + CI-safe)
  full   -> data/nswgov_df.csv + data/rentboard_df.csv (the real 516MB/63MB files)

Inputs come from the local filesystem by default (DATA_DIR, the compose bind
mount). In the cloud (s12 Phase C) the full CSVs live in S3 instead: set
DATA_S3_BUCKET (+ optional DATA_S3_PREFIX) and the `full` source streams each
object straight from S3 — no 580MB download to local disk. The sample source
always stays local (it's committed and tiny).

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

# When set, the `full` source reads the CSVs from S3 instead of DATA_DIR.
DATA_S3_BUCKET = os.environ.get("DATA_S3_BUCKET")
DATA_S3_PREFIX = os.environ.get("DATA_S3_PREFIX", "").strip("/")


class S3Csv:
    """A CSV object in S3, streamed lazily (mirrors the Path API `ingest` uses)."""

    def __init__(self, bucket: str, key: str) -> None:
        self.bucket = bucket
        self.key = key
        self.name = key.rsplit("/", 1)[-1]

    def exists(self) -> bool:
        import botocore.exceptions

        try:
            _s3_client().head_object(Bucket=self.bucket, Key=self.key)
            return True
        except botocore.exceptions.ClientError:
            return False

    def __str__(self) -> str:
        return f"s3://{self.bucket}/{self.key}"


_S3 = None


def _s3_client():
    global _S3
    if _S3 is None:
        import boto3

        _S3 = boto3.client("s3")
    return _S3


def _full_inputs() -> dict[str, Path | S3Csv]:
    names = {"property_sales": "nswgov_df.csv", "property_rent": "rentboard_df.csv"}
    if DATA_S3_BUCKET:
        prefix = f"{DATA_S3_PREFIX}/" if DATA_S3_PREFIX else ""
        return {k: S3Csv(DATA_S3_BUCKET, f"{prefix}{v}") for k, v in names.items()}
    return {k: DATA_DIR / v for k, v in names.items()}


FILES: dict[str, dict[str, Path | S3Csv]] = {
    "sample": {
        "property_sales": DATA_DIR / "samples" / "nsw_sales_sample.csv",
        "property_rent": DATA_DIR / "samples" / "nsw_rent_sample.csv",
    },
    "full": _full_inputs(),
}

# Keep dbt in control of typing for the fields it parses.
SALES_TEXT = ["contract_dt", "settle_dt", "sale_price", "postcode", "area_sqm"]
RENT_TEXT = ["lodgement_dt", "postcode", "weekly_rent", "bedrooms"]


def _rows(src: Path | S3Csv) -> Iterator[dict]:
    if isinstance(src, S3Csv):
        import codecs

        body = _s3_client().get_object(Bucket=src.bucket, Key=src.key)["Body"]
        text = codecs.getreader("utf-8")(body)
        yield from csv.DictReader(text)
    else:
        with src.open(newline="") as f:
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
        f"==> dlt ingest ({SOURCE}): {files['property_sales'].name}, {files['property_rent'].name}"
    )
    info = pipeline.run([sales, rent])
    print(info)


if __name__ == "__main__":
    main()
