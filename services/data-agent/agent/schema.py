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

JOIN_HINT = (
    "Both marts have one row per `suburb` and share that key — JOIN them on "
    "suburb to compare sales and rent growth together (e.g. top growth suburbs "
    "for both sale price and rent). SELECT only; Row-Level Security limits rows "
    "to the datasets a user may access (nsw_sales / nsw_rent)."
)

CURATED_SCHEMA_DOC = f"""\
Two marts describe NSW property-market growth by suburb.

Table {SALES_MART} — residential sale-price growth by suburb (dataset nsw_sales).
Columns:
  suburb (text)             — suburb name; JOIN key
  postcode (text)
  first_year, last_year (int)
  first_median_price, last_median_price (numeric) — median sale price AUD
  sales_growth_pct (numeric) — % change in median sale price over the window
  n_sales (int)

Table {RENT_MART} — weekly-rent growth by suburb (dataset nsw_rent).
Columns:
  suburb (text)             — suburb name; JOIN key
  postcode (text)
  first_year, last_year (int)
  first_median_rent, last_median_rent (numeric) — median weekly rent AUD
  rent_growth_pct (numeric) — % change in median weekly rent over the window
  n_bonds (int)

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
