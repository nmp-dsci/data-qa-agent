"""Schema knowledge the agent grounds its SQL in.

Prefers the dbt manifest (real model/column descriptions produced by
`dbt docs generate`, shared into the container at DBT_MANIFEST) so the LLM sees
exactly the marts the pipeline built. Falls back to a curated description when
the manifest is not present (e.g. the offline stub before a pipeline run).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

SALES_MART = "marts.mart_sales_growth"
RENT_MART = "marts.mart_rent_growth"
YIELD_MART = "marts.mart_property_yield"

JOIN_HINT = (
    "postcode — not suburb — is the true join key (postcode<->suburb is not 1:1 "
    "in this data; suburb is a friendly label only). property_type is 'house', "
    "'unit', or 'ALL' (blended); match it on both sides of a JOIN unless the "
    "question is type-specific. JOIN mart_sales_growth and mart_rent_growth on "
    "(postcode, property_type) to compare sales and rent growth. "
    "mart_property_yield already combines both — use it directly for yield "
    "questions, filtering to the latest `year` for a current figure. SELECT "
    "only; Row-Level Security limits rows to the datasets a user may access "
    "(nsw_sales / nsw_rent — mart_property_yield needs both)."
)

CURATED_SCHEMA_DOC = f"""\
Three marts describe the NSW property market by postcode.

Table {SALES_MART} — sale-price growth by postcode + property_type (dataset nsw_sales).
Columns:
  postcode (text)             — JOIN key
  suburb (text)                — dominant suburb name for the postcode; a label, not a join key
  property_type (text)         — 'house', 'unit', or 'ALL' (blended)
  first_year, last_year (int)
  first_median_price, last_median_price (numeric) — median sale price AUD
  sales_growth_pct (numeric)   — % change in median sale price over the window
  n_sales (int)

Table {RENT_MART} — weekly-rent growth by postcode + property_type (dataset nsw_rent).
Columns:
  postcode (text)             — JOIN key
  suburb (text)
  property_type (text)         — 'house', 'unit', or 'ALL' (blended)
  first_year, last_year (int)
  first_median_rent, last_median_rent (numeric) — median weekly rent AUD
  rent_growth_pct (numeric)    — % change in median weekly rent over the window
  n_bonds (int)

Table {YIELD_MART} — gross rental yield by postcode + property_type + year
(spans both nsw_sales and nsw_rent). One row per year (a time series, not a
first-vs-last window like the growth marts).
Columns:
  postcode (text)             — JOIN key
  suburb (text)
  property_type (text)         — 'house', 'unit', or 'ALL' (blended)
  year (int)
  median_price (numeric), median_rent (numeric)
  gross_yield_pct (numeric)    — (median_rent * 52 / median_price) * 100
  n_sales (int), n_bonds (int)

{JOIN_HINT}
"""


def _schema_from_manifest(path: Path) -> str:
    data = json.loads(path.read_text())
    blocks: list[str] = []
    for node in data.get("nodes", {}).values():
        if node.get("resource_type") != "model" or node.get("schema") != "marts":
            continue
        relation = f"{node['schema']}.{node['name']}"
        blocks.append(f"Table {relation} — {(node.get('description') or '').strip()}\nColumns:")
        for col, meta in node.get("columns", {}).items():
            blocks.append(f"  {col} — {(meta.get('description') or '').strip()}")
        blocks.append("")
    if not blocks:
        raise ValueError("no marts models in manifest")
    header = "NSW property-market marts (from dbt docs):\n"
    return header + "\n".join(blocks) + "\n" + JOIN_HINT


def get_schema() -> str:
    manifest = os.environ.get("DBT_MANIFEST")
    if manifest:
        path = Path(manifest)
        if path.exists():
            try:
                return _schema_from_manifest(path)
            except Exception:  # noqa: BLE001 — fall back to the curated doc
                pass
    return CURATED_SCHEMA_DOC
