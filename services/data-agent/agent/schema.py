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

from .tools_explore import explore_grounding

USER_VISIBLE_SCHEMAS = {"marts", "staging"}
ADMIN_SCHEMA_ORDER = {"app": 0, "marts": 1, "staging": 2, "raw": 3}

SALES_MART = "marts.property_sales"
RENT_MART = "marts.property_rent"
STG_SALES = "staging.property_sales"
STG_RENT = "staging.property_rent"
GEO_BRIDGE = "staging.int_postcode_geo"
YIELD_MART = "marts.property_yield"
GEO_DIM = "marts.dim_postcode_geo"
RAW_SALES = "raw.property_sales"
RAW_RENT = "raw.property_rent"

# Dataset-neutral grounding: the generic half of the old property join hint.
# Every dataset-specific quirk (suburb casing, "rent has no suburb", exact join
# keys) now lives in the knowledge pages the agent searches — this block keeps
# only the rules that hold for ANY mart, so prompts no longer hard-code a domain.
GENERIC_GROUNDING = (
    "Grounding (dataset-neutral): SELECT-only, and Row-Level Security limits rows "
    "to the datasets you may access. The marts are pre-aggregated building blocks — "
    "derive rates, growth, rolling averages and ratios yourself from the ADDITIVE "
    "parts (sums and counts); never average an average or sum a median (bucket "
    "medians don't compose across re-aggregation). Aggregate the additive sum/count "
    "columns when you need a figure broader than the mart's grain. Prefer the marts; "
    "drop to record-grain staging tables (large) only for genuinely record-level "
    "questions, always filtered first. Text dimension values have exact casing — "
    "resolve them with the lookup_values tool rather than guessing. Join keys, "
    "non-additive traps and any dataset-specific quirks live in the knowledge pages: "
    "call search_knowledge for them before writing SQL."
)

CURATED_SCHEMA_DOC = f"""\
NSW property-market data, three lineage-aligned tiers.

Table {SALES_MART} — aggregate sales mart by postcode + suburb + property_type
+ area_band + zoning + month (dataset nsw_sales). No precomputed growth% or
yield%; total_sale_value / n_sold composes across any re-aggregation window.
Columns:
  postcode (text)              — join key to rent (with property_type, month)
  suburb (text)                — real dimension from sales; part of the grain
  property_type (text)         — 'house' or 'unit'; no synthetic 'ALL' rows
  area_band (text)             — cleaned lot-size band; part of the grain
  zoning (text)                — planning zone or 'unknown'; part of the grain
  month (date)                 — first-of-month
  total_sale_value (numeric)   — additive sum of sale_price
  n_sold (int)                 — additive count of sales
  avg_sale_price (numeric)     — bucket-level average
  median_sale_price (numeric)  — bucket-level median; not additive
  min_sale_price (numeric), max_sale_price (numeric)

Table {RENT_MART} — aggregate rent mart by postcode + property_type +
bedroom_band + month (dataset nsw_rent). NO suburb column — rent has no
locality in source; resolve a suburb to its postcode via {GEO_BRIDGE} first. No
precomputed growth%.
Columns:
  postcode (text), property_type (text), bedroom_band (text), month (date)
  total_weekly_rent (numeric)  — additive sum of weekly_rent
  n_rented (int)               — additive count of bonds
  avg_weekly_rent (numeric)    — bucket-level average
  median_weekly_rent (numeric) — bucket-level median; not additive
  min_weekly_rent (numeric), max_weekly_rent (numeric)

Table {YIELD_MART} — gross rental yield by postcode + property_type + year
(dataset nsw_yield). Sales JOINed to rent — the combined view neither single mart
can answer. Keeps the additive legs (total_sale_value, n_sold, total_weekly_rent,
n_rented) so a rollup recomputes correctly; gross_yield_pct is a
ratio-of-averages = 52 * avg_weekly_rent / avg_sale_price * 100, NOT an average of
per-row yields. Thin cells floored at n_sold>=5, n_rented>=5.
Columns:
  postcode (text), property_type (text), year (int)
  total_sale_value, n_sold, total_weekly_rent, n_rented  — additive legs
  avg_sale_price, avg_weekly_rent (numeric)              — cell averages
  gross_yield_pct (numeric)                              — derived; re-derive on rollup

Table {GEO_DIM} — postcode -> ABS geography rollups (dataset: any property grant).
One row per postcode: sa2_name, sa3_name, sa4_name, gcc_name, state_name. JOIN on
postcode to roll any mart up to a region (e.g. "rent by SA3", "top-yield SA4").
Columns: postcode, sa2_name, sa3_name, sa4_name, gcc_name, state_name.

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

{GENERIC_GROUNDING}
"""


def _schema_from_manifest(path: Path) -> str:
    data = json.loads(path.read_text())
    blocks: list[str] = []
    for node in data.get("nodes", {}).values():
        if node.get("resource_type") != "model":
            continue
        if "agent_queryable" not in (node.get("tags") or []):
            continue
        relation = f"{node['schema']}.{node.get('alias') or node['name']}"
        blocks.append(f"Table {relation} — {(node.get('description') or '').strip()}\nColumns:")
        for col, meta in node.get("columns", {}).items():
            blocks.append(f"  {col} — {(meta.get('description') or '').strip()}")
        blocks.append("")
    if not blocks:
        raise ValueError("no agent_queryable models in manifest")
    header = "NSW property-market data (from dbt docs):\n"
    return header + "\n".join(blocks) + "\n" + GENERIC_GROUNDING + "\n\n" + explore_grounding()


def get_schema() -> str:
    manifest = os.environ.get("DBT_MANIFEST")
    if manifest:
        path = Path(manifest)
        if path.exists():
            try:
                return _schema_from_manifest(path)
            except Exception:  # noqa: BLE001 — fall back to the curated doc
                pass
    return CURATED_SCHEMA_DOC + "\n" + explore_grounding()


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
    dataset-neutral grounding (marts-vs-staging, additive/non-additive rules).
    Full per-column descriptions live behind the describe_table tool (tier 2),
    and dataset quirks live in the knowledge pages.
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
    return "\n".join(lines) + "\n\n" + GENERIC_GROUNDING


def list_marts() -> str:
    """Lean mart index for the agent prompt (tier 0): table + one-line purpose.

    Names and a one-sentence blurb only — no columns, no per-dataset quirks. The
    agent pulls columns via describe_table and domain grounding via
    search_knowledge, so the prompt cost stays flat as datasets are added (it no
    longer stacks every table's full schema + domain-specific grounding into
    every turn). Replaces get_schema_compact() in the system prompt.
    """
    tables = get_catalog(role="user")
    marts = [t for t in tables if t["schema"] == "marts"]
    staging = [t for t in tables if t["schema"] == "staging"]
    lines = [
        "Tables you can query — call describe_table('<schema.table>') for a table's "
        "columns before you use it. Prefer the marts (pre-aggregated building blocks):",
    ]
    for t in marts:
        rel = f"{t['schema']}.{t['table']}"
        blurb = _first_sentence(t.get("description") or "")
        lines.append(f"  {rel} — {blurb}" if blurb else f"  {rel}")
    for t in staging:
        rel = f"{t['schema']}.{t['table']}"
        blurb = _first_sentence(t.get("description") or "")
        lines.append(f"  {rel} — {blurb}" if blurb else f"  {rel}")
    return "\n".join(lines) + "\n\n" + GENERIC_GROUNDING


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
        "table": "property_sales",
        "description": (
            "Aggregate sales mart by postcode + suburb + property_type + area_band + zoning + "
            "month (dataset nsw_sales). Re-aggregate additive total/count metrics to derive growth."
        ),
        "columns": [
            {"name": "postcode", "type": "text", "description": "join key to rent"},
            {"name": "suburb", "type": "text", "description": "real dimension; part of the grain"},
            {"name": "property_type", "type": "text", "description": "'house' or 'unit'"},
            {"name": "area_band", "type": "text", "description": "cleaned lot-size band"},
            {"name": "zoning", "type": "text", "description": "NSW planning zone or 'unknown'"},
            {"name": "month", "type": "date", "description": "first-of-month"},
            {"name": "total_sale_value", "type": "numeric", "description": "sum of sale_price"},
            {"name": "n_sold", "type": "integer", "description": "count of sales"},
            {"name": "avg_sale_price", "type": "numeric", "description": "bucket-level average"},
            {"name": "median_sale_price", "type": "numeric", "description": "bucket-level median"},
            {"name": "min_sale_price", "type": "numeric", "description": "bucket-level minimum"},
            {"name": "max_sale_price", "type": "numeric", "description": "bucket-level maximum"},
        ],
    },
    {
        "schema": "marts",
        "table": "property_rent",
        "description": (
            "Aggregate rent mart by postcode + property_type + bedroom_band + month "
            "(dataset nsw_rent). "
            "No suburb column — resolve a suburb to its postcode via int_postcode_geo first."
        ),
        "columns": [
            {"name": "postcode", "type": "text", "description": None},
            {"name": "property_type", "type": "text", "description": "'house' or 'unit'"},
            {"name": "bedroom_band", "type": "text", "description": "'0'..'4', '5+' or 'unknown'"},
            {"name": "month", "type": "date", "description": "first-of-month"},
            {"name": "total_weekly_rent", "type": "numeric", "description": "sum of weekly_rent"},
            {"name": "n_rented", "type": "integer", "description": "count of bonds"},
            {"name": "avg_weekly_rent", "type": "numeric", "description": "bucket-level average"},
            {"name": "median_weekly_rent", "type": "numeric", "description": "bucket-level median"},
            {"name": "min_weekly_rent", "type": "numeric", "description": "bucket-level minimum"},
            {"name": "max_weekly_rent", "type": "numeric", "description": "bucket-level maximum"},
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
        "table": "property_sales",
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
        "table": "property_rent",
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
        "table": "property_sales",
        "description": (
            "Landing table loaded by dlt from the NSW Government property sales CSV. "
            "Prefer staging.property_sales for governed, typed analysis."
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
        "table": "property_rent",
        "description": (
            "Landing table loaded by dlt from the NSW Rental Bond Board CSV. "
            "Prefer staging.property_rent for governed, typed analysis."
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
        "description": "Local identity mirror for dev-auth and Google Sign-in users.",
        "columns": _cols(
            ("id", "uuid"),
            ("auth_provider", "text"),
            ("external_id", "text"),
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
                "table": node.get("alias") or node["name"],
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
