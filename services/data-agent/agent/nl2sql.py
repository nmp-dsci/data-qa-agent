"""Deterministic offline NL->SQL for the NSW property-market marts.

The Phase-0/2b stand-in for the LLM: it recognises the sales/rent/yield intents,
detects a house-vs-unit filter, builds a governed SELECT, and phrases an answer.
When a provider key is configured (LLM_PROVIDER — see provider.py) the real LLM
agent is used instead, and it authors its own SQL from the dbt-manifest schema
(see llm_agent.py) — including computing growth/yield itself, since the marts
hold no precomputed growth%/yield% column (data pipeline refactor). This stub
has to do the same computation explicitly, in SQL, since there's no model here
to write it ad hoc.
"""

from __future__ import annotations

from typing import Any

from .schema import GEO_BRIDGE, RENT_MART, SALES_MART, YIELD_MART

# Dominant suburb per postcode, for a display label. postcode <-> suburb is not
# 1:1, so growth is computed at postcode level (below) and this just picks the
# busiest suburb name to show — the same "order by n_sales desc" the bridge
# documents. Shared by every growth query.
_GEO_CTE = (
    "geo AS (SELECT DISTINCT ON (postcode) postcode, suburb "
    f"FROM {GEO_BRIDGE} ORDER BY postcode, n_sales DESC)"
)


def _property_type(q: str) -> str:
    if any(w in q for w in ("unit", "apartment", "flat")):
        return "unit"
    if "house" in q:
        return "house"
    return "ALL"


def _mentions(q: str, *words: str) -> bool:
    return any(w in q for w in words)


def _sales_trend_sql(q: str, ptype: str) -> str:
    suburbs: list[str] = []
    if "normanhurst" in q:
        suburbs.append("NORMANHURST")
    if "hornsby" in q:
        suburbs.append("HORNSBY")

    filters = [f"property_type = '{ptype}'"]
    if suburbs:
        quoted = ", ".join(f"'{s}'" for s in suburbs)
        filters.append(f"upper(suburb) IN ({quoted})")
    if "2010" in q or "all time" in q:
        filters.append("month >= DATE '2010-01-01'")
    if "2026" in q or "all time" in q:
        filters.append("month <= DATE '2026-12-31'")

    return (
        "SELECT suburb, property_type, month, "
        "round((sum(total_sale_value) / NULLIF(sum(n_sold), 0))::numeric) AS avg_sale_price, "
        "sum(n_sold) AS n_sold "
        f"FROM {SALES_MART} "
        f"WHERE {' AND '.join(filters)} "
        "GROUP BY suburb, property_type, month "
        "ORDER BY suburb, month"
    )


def _growth_ctes(mart: str, value_col: str, count_col: str, ptype: str, prefix: str) -> str:
    """CTEs computing first-vs-last-available-month growth% per postcode.

    Aggregates across suburbs (sum totals / sum counts) so the figure is
    postcode-level even though mart_sales_summary is now suburb-grained; total/
    count (not median) so the average composes across both the time window and
    the suburbs — the reasoning documented in mart_sales_summary.sql for why
    growth isn't a precomputed column. mart_rent_summary has no suburb, so the
    same grouping is naturally postcode-level there.
    """
    return f"""
{prefix}bounds AS (
    SELECT postcode, min(month) AS first_month, max(month) AS last_month
    FROM {mart} WHERE property_type = '{ptype}' GROUP BY postcode
),
{prefix}first AS (
    SELECT b.postcode, sum(m.{value_col}) / NULLIF(sum(m.{count_col}), 0) AS avg_value
    FROM {prefix}bounds b
    JOIN {mart} m ON m.postcode = b.postcode AND m.property_type = '{ptype}'
        AND m.month = b.first_month
    GROUP BY b.postcode
),
{prefix}last AS (
    SELECT b.postcode, sum(m.{value_col}) / NULLIF(sum(m.{count_col}), 0) AS avg_value
    FROM {prefix}bounds b
    JOIN {mart} m ON m.postcode = b.postcode AND m.property_type = '{ptype}'
        AND m.month = b.last_month
    GROUP BY b.postcode
),
{prefix}growth AS (
    SELECT l.postcode, l.avg_value,
        round((l.avg_value - f.avg_value) / NULLIF(f.avg_value, 0) * 100, 1) AS growth_pct
    FROM {prefix}last l JOIN {prefix}first f ON f.postcode = l.postcode
)"""


def build_sql(question: str) -> tuple[str, str]:
    """Return (sql, intent) for a natural-language question."""
    q = question.lower()
    ptype = _property_type(q)

    if _mentions(q, "how many", "count", "number of"):
        return (
            f"SELECT count(DISTINCT postcode) AS count FROM {SALES_MART} "
            "WHERE property_type = 'ALL'",
            "count",
        )

    wants_yield = _mentions(q, "yield", "return on", "roi")
    wants_sales = _mentions(q, "sale", "price", "buy", "purchase")
    wants_rent = _mentions(q, "rent", "rental", "bond")
    wants_trend = _mentions(q, "trend", "over time", "by month", "monthly", "time series")

    if wants_yield:
        return (
            f"SELECT suburb, postcode, property_type, month, median_price, median_rent, "
            f"round((median_rent * 52 / median_price * 100)::numeric, 2) AS gross_yield_pct "
            f"FROM {YIELD_MART} "
            f"WHERE property_type = '{ptype}' AND month = (SELECT max(month) FROM {YIELD_MART}) "
            f"ORDER BY gross_yield_pct DESC LIMIT 10",
            "yield",
        )

    # Rent-only vs sales-only vs both. "Growth suburbs for sales and rent" -> combined.
    # suburb comes from the geo bridge (dominant label) since growth is
    # postcode-level and rent has no suburb of its own.
    if wants_sales and wants_trend and not wants_rent:
        return (_sales_trend_sql(q, ptype), "sales_trend")

    if wants_rent and not wants_sales:
        ctes = _growth_ctes(RENT_MART, "total_weekly_rent", "n_rented", ptype, "r_")
        return (
            f"WITH {_GEO_CTE},{ctes}\n"
            "SELECT g.suburb, r.postcode, r.growth_pct AS rent_growth_pct, "
            "round(r.avg_value) AS last_avg_rent "
            "FROM r_growth r JOIN geo g ON g.postcode = r.postcode "
            "ORDER BY rent_growth_pct DESC LIMIT 10",
            "rent",
        )
    if wants_sales and not wants_rent:
        ctes = _growth_ctes(SALES_MART, "total_sale_value", "n_sold", ptype, "s_")
        return (
            f"WITH {_GEO_CTE},{ctes}\n"
            "SELECT g.suburb, s.postcode, s.growth_pct AS sales_growth_pct, "
            "round(s.avg_value) AS last_avg_price "
            "FROM s_growth s JOIN geo g ON g.postcode = s.postcode "
            "ORDER BY sales_growth_pct DESC LIMIT 10",
            "sales",
        )

    s_ctes = _growth_ctes(SALES_MART, "total_sale_value", "n_sold", ptype, "s_")
    r_ctes = _growth_ctes(RENT_MART, "total_weekly_rent", "n_rented", ptype, "r_")
    return (
        f"WITH {_GEO_CTE},{s_ctes},{r_ctes}\n"
        "SELECT g.suburb, s.postcode, s.growth_pct AS sales_growth_pct, "
        "r.growth_pct AS rent_growth_pct, "
        "round((s.growth_pct + r.growth_pct) / 2, 1) AS combined_growth_pct "
        "FROM s_growth s JOIN r_growth r ON r.postcode = s.postcode "
        "JOIN geo g ON g.postcode = s.postcode "
        "ORDER BY combined_growth_pct DESC LIMIT 10",
        "combined",
    )


def _pct(v: Any) -> str:
    """Signed percentage, for a change (growth)."""
    try:
        return f"{float(v):+.1f}%"
    except (TypeError, ValueError):
        return str(v)


def _pct_abs(v: Any) -> str:
    """Unsigned percentage, for an absolute figure (yield)."""
    try:
        return f"{float(v):.1f}%"
    except (TypeError, ValueError):
        return str(v)


def _money(v: Any) -> str:
    try:
        return f"${int(round(float(v))):,}"
    except (TypeError, ValueError):
        return str(v)


def phrase_answer(question: str, intent: str, result: dict[str, Any]) -> str:
    rows: list[list[Any]] = result["rows"]
    if result["row_count"] == 0:
        return (
            "No rows are visible to you for that question — your account may not "
            "have access to this dataset. (Row-Level Security returned 0 rows.)"
        )

    if intent == "count":
        return f"There are {int(rows[0][0]):,} postcodes with sale-price data."

    if intent == "sales_trend":
        suburbs = sorted({str(r[0]) for r in rows if r and r[0]})
        first_month = rows[0][2]
        last_month = rows[-1][2]
        return (
            f"Monthly sale-price trend for {', '.join(suburbs)} from {first_month} "
            f"to {last_month}. Showing {result['row_count']} monthly rows below."
        )

    top = rows[:5]
    if intent == "combined":
        parts = [f"{r[0]} (sales {_pct(r[2])}, rent {_pct(r[3])})" for r in top]
        return (
            "Top growth areas across both markets — " + "; ".join(parts) + ". "
            f"Showing {result['row_count']} rows below."
        )
    if intent == "sales":
        parts = [f"{r[0]} ({_pct(r[2])}, now {_money(r[3])})" for r in top]
        return "Top areas by sale-price growth — " + "; ".join(parts) + "."
    if intent == "rent":
        parts = [f"{r[0]} ({_pct(r[2])}, now {_money(r[3])}/wk)" for r in top]
        return "Top areas by rent growth — " + "; ".join(parts) + "."
    # yield: suburb, postcode, property_type, month, median_price, median_rent, gross_yield_pct
    parts = [f"{r[0]} ({_pct_abs(r[6])} gross)" for r in top]
    return "Top areas by gross rental yield — " + "; ".join(parts) + "."
