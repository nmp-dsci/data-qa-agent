"""Schema knowledge the agent grounds its SQL in.

Prefers the dbt manifest (real model/column descriptions produced by
`dbt docs generate`, shared into the container at DBT_MANIFEST) so the LLM sees
exactly the tables the pipeline built and tagged `agent_queryable` — marts
(summary building blocks) and the two widened staging tables (record grain),
not just marts. Falls back to a curated description when the manifest is not
present (e.g. the offline stub before a pipeline run).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

SALES_MART = "marts.mart_sales_summary"
RENT_MART = "marts.mart_rent_summary"
YIELD_MART = "marts.mart_property_yield"
STG_SALES = "staging.stg_sales"
STG_RENT = "staging.stg_rent"
GEO_BRIDGE = "staging.int_postcode_geo"

JOIN_HINT = (
    "Sales/yield tables carry a real suburb dimension (from the sale records) "
    "— filter by suburb for one locality, but postcode<->suburb is not 1:1, so "
    "to get a postcode total SUM total_sale_value/n_sold across that postcode's "
    "suburbs (additive; median_price is not). RENT has NO suburb (raw.rent has "
    "no locality): for a rent-by-suburb question, first resolve the suburb to "
    f"its postcode(s) via {GEO_BRIDGE} (WHERE suburb ILIKE '%name%'), then "
    "query rent by postcode. Join sales<->rent on (postcode, property_type, "
    "month) — NOT suburb (rent has none). property_type is 'house', 'unit', or "
    "'ALL' (blended); match it on both sides unless the question is "
    "type-specific. month is a first-of-month date. Default to the marts "
    "(small, precomputed sum/count/median) — compute growth over any window, "
    "rolling averages, and yield yourself from total_sale_value/n_sold, "
    "total_weekly_rent/n_rented, and median_price/median_rent; none are "
    "pre-baked. Buckets are kept even when tiny, so filter WHERE n_sold "
    f">= N (or n_rented) when a median must be reliable. Only drop to {STG_SALES}"
    f"/{STG_RENT} (record grain, ~3M rows) for genuinely record-level questions "
    "(individual sales/bonds, addresses, bedroom counts, lot-size bands) — "
    "always filter by postcode and/or month first. SELECT only; Row-Level "
    "Security limits rows to the datasets a user may access (nsw_sales / "
    f"nsw_rent — {YIELD_MART} needs both)."
)

CURATED_SCHEMA_DOC = f"""\
NSW property-market data, two tiers.

Table {SALES_MART} — sale summary building block by postcode + suburb +
property_type + month (dataset nsw_sales). No precomputed growth% —
total_sale_value / n_sold composes across any window.
Columns:
  postcode (text)              — join key to rent (with property_type, month)
  suburb (text)                — real dimension; part of the grain (filter for one locality)
  property_type (text)         — 'house', 'unit', or 'ALL' (blended)
  month (date)                 — first-of-month
  total_sale_value (numeric)   — sum of sale_price that month
  n_sold (int)                 — count of sales that month
  median_price (numeric)       — median sale price AUD that month

Table {RENT_MART} — rent summary building block by postcode + property_type
+ month (dataset nsw_rent). NO suburb column — rent has no locality in source;
resolve a suburb to its postcode via {GEO_BRIDGE} first. No precomputed growth%.
Columns:
  postcode (text), property_type (text), month (date)
  total_weekly_rent (numeric)  — sum of weekly_rent that month
  n_rented (int)                — count of bonds that month
  median_rent (numeric)        — median weekly rent AUD that month

Table {YIELD_MART} — {SALES_MART} and {RENT_MART} pre-joined on (postcode, property_type, month)
(spans both nsw_sales and nsw_rent). Grain is postcode+suburb+property_type+month
(suburb from the sales side). No precomputed gross_yield_pct — compute it as
(median_rent * 52 / median_price) * 100. Rent columns are postcode-level
repeated per suburb — don't sum them across suburbs.
Columns: postcode, suburb, property_type, month, total_sale_value, n_sold,
median_price, total_weekly_rent, n_rented, median_rent.

Table {GEO_BRIDGE} — postcode<->suburb bridge (dataset nsw_sales). Every
(postcode, suburb) pair, with n_sales. Use it to resolve a suburb name to its
postcode(s), especially for rent questions (rent has no suburb).
Columns: postcode, suburb, n_sales.

Table {STG_SALES} — record-grain NSW sales, ~3M rows (dataset nsw_sales). One row per sale.
Use only for record-level questions, always filtered by postcode/month.
Columns: sale_id, property_id, suburb, postcode, property_type, sale_date,
sale_year, sale_month, sale_price, area_sqm, area_band, area_type, zoning,
house_no, street_name, unit_no, prop_name.

Table {STG_RENT} — record-grain NSW rental bonds, ~3M rows (dataset nsw_rent). One row per bond.
Use only for record-level questions, always filtered by postcode/month.
Columns: rent_id, rent_date, rent_year, rent_month, postcode,
property_type_code, property_type, bedrooms, weekly_rent.

{JOIN_HINT}
"""


def _schema_from_manifest(path: Path) -> str:
    data = json.loads(path.read_text())
    blocks: list[str] = []
    for node in data.get("nodes", {}).values():
        if node.get("resource_type") != "model":
            continue
        if "agent_queryable" not in (node.get("tags") or []):
            continue
        relation = f"{node['schema']}.{node['name']}"
        blocks.append(f"Table {relation} — {(node.get('description') or '').strip()}\nColumns:")
        for col, meta in node.get("columns", {}).items():
            blocks.append(f"  {col} — {(meta.get('description') or '').strip()}")
        blocks.append("")
    if not blocks:
        raise ValueError("no agent_queryable models in manifest")
    header = "NSW property-market data (from dbt docs):\n"
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
