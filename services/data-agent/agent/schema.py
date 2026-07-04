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
from typing import Any

USER_VISIBLE_SCHEMAS = {"marts", "staging"}
ADMIN_SCHEMA_ORDER = {"app": 0, "marts": 1, "staging": 2, "raw": 3}

SALES_MART = "marts.mart_sales_summary"
RENT_MART = "marts.mart_rent_summary"
RENT_BEDROOM_MART = "marts.mart_rent_by_bedroom"
SALES_SEGMENT_MART = "marts.mart_sales_by_segment"
YIELD_MART = "marts.mart_property_yield"
STG_SALES = "staging.stg_sales"
STG_RENT = "staging.stg_rent"
GEO_BRIDGE = "staging.int_postcode_geo"
RAW_SALES = "raw.sales"
RAW_RENT = "raw.rent"

JOIN_HINT = (
    "suburb values are stored Title Case (e.g. 'Hornsby', 'Normanhurst'), NOT "
    "upper-case — never match them with `suburb IN ('HORNSBY', ...)` (returns "
    "zero rows); use UPPER(suburb) IN ('HORNSBY', ...) or ILIKE, or resolve the "
    f"exact spelling once via the lookup_values tool. "
    "Sales/yield tables carry a real suburb dimension (from the sale records) "
    "— filter by suburb for one locality, but postcode<->suburb is not 1:1, so "
    "to get a postcode total SUM total_sale_value/n_sold across that postcode's "
    "suburbs (additive; median_price is not). RENT has NO suburb (raw.rent has "
    "no locality): for a rent-by-suburb question, first resolve the suburb to "
    f"its postcode(s) via {GEO_BRIDGE} (WHERE suburb ILIKE '%name%'), then "
    "query rent by postcode. Join sales<->rent on (postcode, property_type, "
    "month) — NOT suburb (rent has none). property_type is 'house', 'unit', or "
    "'ALL' (blended); match it on both sides unless the question is "
    "type-specific. month is a first-of-month date. For a breakdown by bedroom "
    f"count use {RENT_BEDROOM_MART}, and by lot-size band or planning zone use "
    f"{SALES_SEGMENT_MART}; in those two, bedroom_band / area_band / zoning are "
    "part of the grain (always a specific value, never 'ALL') so never SUM "
    "across them — for an all-bedroom or all-segment figure use the plain "
    f"{RENT_MART}/{SALES_MART} instead. Default to the marts "
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

Table {RENT_BEDROOM_MART} — {RENT_MART} broken out by BEDROOM band (dataset
nsw_rent). Grain postcode + property_type + bedroom_band + month. bedroom_band is
'0'..'4', '5+' or 'unknown' and is part of the grain (no 'ALL' bedroom row) —
never SUM across it; use {RENT_MART} for all-bedroom figures. Use this for
"rent by bedroom" questions.
Columns: postcode, property_type, bedroom_band (text), month, total_weekly_rent,
n_rented, median_rent.

Table {SALES_SEGMENT_MART} — {SALES_MART} broken out by lot-size band and
planning zone (dataset nsw_sales). Grain postcode + suburb + property_type +
area_band + zoning + month. area_band ('<400'..'5000+','unknown') and zoning (NSW
zone code e.g. R2, RU5, or 'unknown') are part of the grain (no 'ALL' row) —
never SUM across them; use {SALES_MART} for all-segment figures. Use this for
"price by lot size" / "price by zone" questions.
Columns: postcode, suburb, property_type, area_band (text), zoning (text), month,
total_sale_value, n_sold, median_price.

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
sale_year, sale_month, sale_price, area_sqm (standardised to sqm via area_type),
area_band ('<400'..'5000+'), area_type ('H'/'M'), zoning,
house_no, street_name, unit_no, prop_name.

Table {STG_RENT} — record-grain NSW rental bonds, ~3M rows (dataset nsw_rent). One row per bond.
Use only for record-level questions, always filtered by postcode/month.
Columns: rent_id, rent_date, rent_year, rent_month, postcode,
property_type_code, property_type, bedrooms, bedroom_band ('0'..'5+'/'unknown'),
weekly_rent.

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


def _first_sentence(text: str, limit: int = 160) -> str:
    """One-line orientation blurb: first sentence, capped at a word boundary.

    The full dbt prose for one table runs ~1-2k chars; pinning all of it for
    every queryable table into every model turn is what made the schema block
    ~15k chars. The compact catalog keeps just this blurb + column names, and the
    agent pulls full column docs on demand via describe_table.
    """
    text = " ".join((text or "").split())
    if not text:
        return ""
    # First sentence, but don't be fooled by "e.g." / "R2," style mid-abbreviations:
    # cut on ". " only when the next char starts a capitalised word.
    for i in range(len(text) - 2):
        if text[i] == "." and text[i + 1] == " " and text[i + 2].isupper():
            return text[: i + 1]
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;— ") + "…"


def get_schema_compact() -> str:
    """A compact table catalog for the agent's system prompt (tier 1).

    One line of orientation + the column names per queryable table, plus the
    dense JOIN_HINT (the highest-value grounding — suburb casing, rent has no
    suburb, don't SUM across a grain dimension, marts-vs-staging). Full per-column
    descriptions live behind the describe_table tool (tier 2), so this stays a
    few thousand chars instead of ~15k, cutting the per-turn base cost sharply.
    """
    tables = get_catalog(role="user")
    lines = [
        "NSW property-market data — compact catalog. Column names are listed; call "
        "describe_table('<schema.table>') for full column descriptions when unsure.",
        "",
    ]
    for t in tables:
        rel = f"{t['schema']}.{t['table']}"
        blurb = _first_sentence(t.get("description") or "")
        cols = ", ".join(c["name"] for c in t.get("columns", []))
        lines.append(f"Table {rel} — {blurb}" if blurb else f"Table {rel}")
        lines.append(f"  cols: {cols}")
    return "\n".join(lines) + "\n\n" + JOIN_HINT


def describe_table(name: str) -> str:
    """Full column-level docs for one table (tier 2, fetched on demand).

    Accepts 'schema.table' or a bare 'table'. Sourced from the same catalog as
    the compact schema, so it prefers the live dbt manifest and falls back to the
    curated catalog offline.
    """
    key = name.strip()
    tables = get_catalog(role="user")
    for t in tables:
        rel = f"{t['schema']}.{t['table']}"
        if key in (rel, t["table"]):
            out = [f"Table {rel} — {(t.get('description') or '').strip()}", "Columns:"]
            for c in t.get("columns", []):
                typ = c.get("type") or ""
                desc = (c.get("description") or "").strip()
                head = f"  {c['name']} ({typ})" if typ else f"  {c['name']}"
                out.append(f"{head} — {desc}" if desc else head)
            return "\n".join(out)
    available = ", ".join(f"{t['schema']}.{t['table']}" for t in tables)
    return f"No table named {name!r}. Available: {available}"


# ---------------------------------------------------------------------------
# Structured catalog — same knowledge as get_schema(), but as data the SQL
# editor's schema browser (and CodeMirror autocomplete) can render. Prefers the
# dbt manifest; falls back to a curated list mirroring CURATED_SCHEMA_DOC so the
# browser still works offline (e.g. before a pipeline run).
# ---------------------------------------------------------------------------

# Each entry: {schema, table, description, columns: [{name, type, description}]}.
CURATED_CATALOG: list[dict[str, Any]] = [
    {
        "schema": "marts",
        "table": "mart_sales_summary",
        "description": (
            "Sale summary building block by postcode + suburb + property_type + month "
            "(dataset nsw_sales). Compose growth over any window from total_sale_value / n_sold."
        ),
        "columns": [
            {"name": "postcode", "type": "text", "description": "join key to rent"},
            {"name": "suburb", "type": "text", "description": "real dimension; part of the grain"},
            {"name": "property_type", "type": "text", "description": "'house', 'unit', or 'ALL'"},
            {"name": "month", "type": "date", "description": "first-of-month"},
            {"name": "total_sale_value", "type": "numeric", "description": "sum of sale_price"},
            {"name": "n_sold", "type": "integer", "description": "count of sales that month"},
            {"name": "median_price", "type": "numeric", "description": "median sale price AUD"},
        ],
    },
    {
        "schema": "marts",
        "table": "mart_rent_summary",
        "description": (
            "Rent summary building block by postcode + property_type + month (dataset nsw_rent). "
            "No suburb column — resolve a suburb to its postcode via int_postcode_geo first."
        ),
        "columns": [
            {"name": "postcode", "type": "text", "description": None},
            {"name": "property_type", "type": "text", "description": "'house', 'unit', or 'ALL'"},
            {"name": "month", "type": "date", "description": "first-of-month"},
            {"name": "total_weekly_rent", "type": "numeric", "description": "sum of weekly_rent"},
            {"name": "n_rented", "type": "integer", "description": "count of bonds that month"},
            {"name": "median_rent", "type": "numeric", "description": "median weekly rent AUD"},
        ],
    },
    {
        "schema": "marts",
        "table": "mart_rent_by_bedroom",
        "description": (
            "mart_rent_summary broken out by bedroom band (dataset nsw_rent). bedroom_band is part "
            "of the grain (no 'ALL' row) — never SUM across it."
        ),
        "columns": [
            {"name": "postcode", "type": "text", "description": None},
            {"name": "property_type", "type": "text", "description": None},
            {"name": "bedroom_band", "type": "text", "description": "'0'..'4', '5+' or 'unknown'"},
            {"name": "month", "type": "date", "description": "first-of-month"},
            {"name": "total_weekly_rent", "type": "numeric", "description": None},
            {"name": "n_rented", "type": "integer", "description": None},
            {"name": "median_rent", "type": "numeric", "description": None},
        ],
    },
    {
        "schema": "marts",
        "table": "mart_sales_by_segment",
        "description": (
            "mart_sales_summary broken out by lot-size band and planning zone (dataset nsw_sales). "
            "area_band and zoning are part of the grain (no 'ALL' row) — never SUM across them."
        ),
        "columns": [
            {"name": "postcode", "type": "text", "description": None},
            {"name": "suburb", "type": "text", "description": None},
            {"name": "property_type", "type": "text", "description": None},
            {"name": "area_band", "type": "text", "description": "'<400'..'5000+', 'unknown'"},
            {"name": "zoning", "type": "text", "description": "NSW zone code e.g. R2, RU5"},
            {"name": "month", "type": "date", "description": "first-of-month"},
            {"name": "total_sale_value", "type": "numeric", "description": None},
            {"name": "n_sold", "type": "integer", "description": None},
            {"name": "median_price", "type": "numeric", "description": None},
        ],
    },
    {
        "schema": "marts",
        "table": "mart_property_yield",
        "description": (
            "mart_sales_summary and mart_rent_summary pre-joined on (postcode, property_type, "
            "month). Compute gross_yield_pct = (median_rent * 52 / median_price) * 100."
        ),
        "columns": [
            {"name": "postcode", "type": "text", "description": None},
            {"name": "suburb", "type": "text", "description": "from the sales side"},
            {"name": "property_type", "type": "text", "description": None},
            {"name": "month", "type": "date", "description": "first-of-month"},
            {"name": "total_sale_value", "type": "numeric", "description": None},
            {"name": "n_sold", "type": "integer", "description": None},
            {"name": "median_price", "type": "numeric", "description": None},
            {"name": "total_weekly_rent", "type": "numeric", "description": "postcode-level"},
            {"name": "n_rented", "type": "integer", "description": None},
            {"name": "median_rent", "type": "numeric", "description": "postcode-level"},
        ],
    },
    {
        "schema": "staging",
        "table": "int_postcode_geo",
        "description": (
            "postcode<->suburb bridge (dataset nsw_sales). Resolve a suburb name to its "
            "postcode(s), especially for rent questions (rent has no suburb)."
        ),
        "columns": [
            {"name": "postcode", "type": "text", "description": None},
            {"name": "suburb", "type": "text", "description": None},
            {"name": "n_sales", "type": "integer", "description": None},
        ],
    },
    {
        "schema": "staging",
        "table": "stg_sales",
        "description": (
            "Record-grain NSW sales, ~3M rows (dataset nsw_sales). One row per sale — use only for "
            "record-level questions, always filtered by postcode/month."
        ),
        "columns": [
            {"name": "sale_id", "type": "text", "description": None},
            {"name": "property_id", "type": "text", "description": None},
            {"name": "suburb", "type": "text", "description": None},
            {"name": "postcode", "type": "text", "description": None},
            {"name": "property_type", "type": "text", "description": None},
            {"name": "sale_date", "type": "date", "description": None},
            {"name": "sale_year", "type": "integer", "description": None},
            {"name": "sale_month", "type": "integer", "description": None},
            {"name": "sale_price", "type": "numeric", "description": None},
            {"name": "area_sqm", "type": "numeric", "description": "standardised to sqm"},
            {"name": "area_band", "type": "text", "description": "'<400'..'5000+'"},
            {"name": "area_type", "type": "text", "description": "'H'/'M'"},
            {"name": "zoning", "type": "text", "description": None},
            {"name": "house_no", "type": "text", "description": None},
            {"name": "street_name", "type": "text", "description": None},
            {"name": "unit_no", "type": "text", "description": None},
            {"name": "prop_name", "type": "text", "description": None},
        ],
    },
    {
        "schema": "staging",
        "table": "stg_rent",
        "description": (
            "Record-grain NSW rental bonds, ~3M rows (dataset nsw_rent). One row per bond — use "
            "only for record-level questions, always filtered by postcode/month."
        ),
        "columns": [
            {"name": "rent_id", "type": "text", "description": None},
            {"name": "rent_date", "type": "date", "description": None},
            {"name": "rent_year", "type": "integer", "description": None},
            {"name": "rent_month", "type": "integer", "description": None},
            {"name": "postcode", "type": "text", "description": None},
            {"name": "property_type_code", "type": "text", "description": None},
            {"name": "property_type", "type": "text", "description": None},
            {"name": "bedrooms", "type": "integer", "description": None},
            {"name": "bedroom_band", "type": "text", "description": "'0'..'5+'/'unknown'"},
            {"name": "weekly_rent", "type": "numeric", "description": None},
        ],
    },
    {
        "schema": "raw",
        "table": "sales",
        "description": (
            "Landing table loaded by dlt from the NSW Government property sales CSV. "
            "Prefer staging.stg_sales for governed, typed analysis."
        ),
        "columns": [
            {"name": "property_id", "type": "text", "description": None},
            {"name": "locality", "type": "text", "description": "raw suburb/locality"},
            {"name": "postcode", "type": "text", "description": None},
            {"name": "contract_dt", "type": "text", "description": "raw contract date"},
            {"name": "sale_price", "type": "text", "description": "raw sale price"},
            {"name": "prop_purpose", "type": "text", "description": None},
            {"name": "strata_no", "type": "text", "description": None},
            {"name": "area_sqm", "type": "text", "description": None},
            {"name": "area_type", "type": "text", "description": None},
            {"name": "zoning", "type": "text", "description": None},
            {"name": "house_no", "type": "text", "description": None},
            {"name": "street_name", "type": "text", "description": None},
            {"name": "unit_no", "type": "text", "description": None},
            {"name": "prop_name", "type": "text", "description": None},
        ],
    },
    {
        "schema": "raw",
        "table": "rent",
        "description": (
            "Landing table loaded by dlt from the NSW Rental Bond Board CSV. "
            "Prefer staging.stg_rent for governed, typed analysis."
        ),
        "columns": [
            {"name": "lodgement_dt", "type": "text", "description": "raw lodgement date"},
            {"name": "postcode", "type": "text", "description": None},
            {"name": "property_type", "type": "text", "description": "raw source code"},
            {"name": "bedrooms", "type": "text", "description": "raw bedroom count"},
            {"name": "weekly_rent", "type": "text", "description": "raw weekly rent"},
        ],
    },
]


def _cols(*columns: tuple[str, str]) -> list[dict[str, Any]]:
    return [{"name": name, "type": typ, "description": None} for name, typ in columns]


APP_CATALOG: list[dict[str, Any]] = [
    {
        "schema": "app",
        "table": "schema_migrations",
        "description": "Alembic/application migration audit table.",
        "columns": _cols(("version", "text"), ("applied_at", "timestamp with time zone")),
    },
    {
        "schema": "app",
        "table": "users",
        "description": "Local identity mirror for dev-auth and Entra users.",
        "columns": _cols(
            ("id", "uuid"),
            ("entra_oid", "text"),
            ("username", "text"),
            ("email", "text"),
            ("display_name", "text"),
            ("role", "text"),
            ("created_at", "timestamp with time zone"),
        ),
    },
    {
        "schema": "app",
        "table": "datasets",
        "description": "Dataset registry populated by migrations and the pipeline.",
        "columns": _cols(
            ("id", "uuid"),
            ("slug", "text"),
            ("name", "text"),
            ("description", "text"),
            ("status", "text"),
            ("row_count", "integer"),
            ("created_at", "timestamp with time zone"),
        ),
    },
    {
        "schema": "app",
        "table": "dataset_access",
        "description": "Per-user dataset grants used by RLS policies.",
        "columns": _cols(
            ("id", "uuid"),
            ("dataset_id", "uuid"),
            ("user_id", "uuid"),
            ("access", "text"),
        ),
    },
    {
        "schema": "app",
        "table": "conversations",
        "description": "Chat conversation headers.",
        "columns": _cols(
            ("id", "uuid"),
            ("user_id", "uuid"),
            ("dataset_id", "uuid"),
            ("title", "text"),
            ("created_at", "timestamp with time zone"),
        ),
    },
    {
        "schema": "app",
        "table": "messages",
        "description": "Chat turns and generated SQL attached to conversations.",
        "columns": _cols(
            ("id", "uuid"),
            ("conversation_id", "uuid"),
            ("user_id", "uuid"),
            ("role", "text"),
            ("content", "text"),
            ("sql_generated", "text"),
            ("tokens", "integer"),
            ("latency_ms", "integer"),
            ("created_at", "timestamp with time zone"),
        ),
    },
    {
        "schema": "app",
        "table": "query_runs",
        "description": "Audit trail for agent and SQL editor query execution.",
        "columns": _cols(
            ("id", "uuid"),
            ("conversation_id", "uuid"),
            ("message_id", "uuid"),
            ("user_id", "uuid"),
            ("dataset_id", "uuid"),
            ("question", "text"),
            ("sql_text", "text"),
            ("engine", "text"),
            ("row_count", "integer"),
            ("latency_ms", "integer"),
            ("status", "text"),
            ("error", "text"),
            ("created_at", "timestamp with time zone"),
            ("input_tokens", "integer"),
            ("output_tokens", "integer"),
            ("source", "text"),
            ("channel", "text"),
            ("trace", "jsonb"),
        ),
    },
    {
        "schema": "app",
        "table": "user_memories",
        "description": "Per-user agent memories with pgvector embeddings.",
        "columns": _cols(
            ("id", "uuid"),
            ("user_id", "uuid"),
            ("kind", "text"),
            ("content", "text"),
            ("embedding", "vector(384)"),
            ("created_at", "timestamp with time zone"),
            ("last_used_at", "timestamp with time zone"),
        ),
    },
    {
        "schema": "app",
        "table": "events",
        "description": "Product analytics and journey event stream.",
        "columns": _cols(
            ("id", "uuid"),
            ("user_id", "uuid"),
            ("session_id", "text"),
            ("event_type", "text"),
            ("payload", "jsonb"),
            ("created_at", "timestamp with time zone"),
        ),
    },
]


def _copy_table(table: dict[str, Any]) -> dict[str, Any]:
    return {**table, "columns": [dict(col) for col in table.get("columns", [])]}


def sort_catalog(catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        catalog,
        key=lambda t: (ADMIN_SCHEMA_ORDER.get(t["schema"], 99), t["schema"], t["table"]),
    )


def merge_catalogs(
    primary: list[dict[str, Any]], fallback: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Merge live catalog metadata over a baseline, keyed by schema/table."""
    merged = {(t["schema"], t["table"]): _copy_table(t) for t in fallback}
    for table in primary:
        merged[(table["schema"], table["table"])] = _copy_table(table)
    return sort_catalog(list(merged.values()))


def admin_catalog(catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Admin sidebar baseline: app metadata plus all data catalog entries."""
    return merge_catalogs(catalog, APP_CATALOG)


def _catalog_from_manifest(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    tables: list[dict[str, Any]] = []
    for node in data.get("nodes", {}).values():
        if node.get("resource_type") != "model":
            continue
        if "agent_queryable" not in (node.get("tags") or []):
            continue
        columns = [
            {
                "name": col,
                "type": (meta.get("data_type") or None),
                "description": (meta.get("description") or "").strip() or None,
            }
            for col, meta in (node.get("columns") or {}).items()
        ]
        tables.append(
            {
                "schema": node["schema"],
                "table": node["name"],
                "description": (node.get("description") or "").strip() or None,
                "columns": columns,
            }
        )
    for source in data.get("sources", {}).values():
        if source.get("resource_type") != "source":
            continue
        if source.get("schema") != "raw":
            continue
        columns = [
            {
                "name": col,
                "type": (meta.get("data_type") or None),
                "description": (meta.get("description") or "").strip() or None,
            }
            for col, meta in (source.get("columns") or {}).items()
        ]
        if not columns:
            continue
        tables.append(
            {
                "schema": source["schema"],
                "table": source["name"],
                "description": (source.get("description") or "").strip() or None,
                "columns": columns,
            }
        )
    existing = {(t["schema"], t["table"]) for t in tables}
    for table in CURATED_CATALOG:
        key = (table["schema"], table["table"])
        if table["schema"] == "raw" and key not in existing:
            tables.append(table)
    if not tables:
        raise ValueError("no agent_queryable models in manifest")
    return sort_catalog(tables)


def filter_catalog_for_role(catalog: list[dict[str, Any]], *, role: str) -> list[dict[str, Any]]:
    """Limit schema-browser metadata to the schemas a non-admin should discover."""
    if role == "admin":
        return catalog
    return [table for table in catalog if table.get("schema") in USER_VISIBLE_SCHEMAS]


def get_catalog(*, role: str = "user") -> list[dict[str, Any]]:
    """Structured schema for the SQL editor's browser + autocomplete."""
    manifest = os.environ.get("DBT_MANIFEST")
    if manifest:
        path = Path(manifest)
        if path.exists():
            try:
                catalog = _catalog_from_manifest(path)
                return (
                    admin_catalog(catalog)
                    if role == "admin"
                    else filter_catalog_for_role(catalog, role=role)
                )
            except Exception:  # noqa: BLE001 — fall back to the curated catalog
                pass
    return (
        admin_catalog(CURATED_CATALOG)
        if role == "admin"
        else filter_catalog_for_role(CURATED_CATALOG, role=role)
    )
