"""Deterministic offline NL->SQL for the NSW property-market marts.

The Phase-0/2b stand-in for the LLM: it recognises the sales/rent/yield intents,
detects a house-vs-unit filter, builds a governed SELECT (including the
sales<->rent JOIN keyed on postcode + property_type), and phrases an answer.
When ANTHROPIC_API_KEY is set the Claude agent is used instead, and it authors
the same kind of JOIN from the dbt-manifest schema (see claude_agent.py).
"""

from __future__ import annotations

from typing import Any

from .schema import RENT_MART, SALES_MART, YIELD_MART


def _property_type(q: str) -> str:
    if any(w in q for w in ("unit", "apartment", "flat")):
        return "unit"
    if "house" in q:
        return "house"
    return "ALL"


def _mentions(q: str, *words: str) -> bool:
    return any(w in q for w in words)


def build_sql(question: str) -> tuple[str, str]:
    """Return (sql, intent) for a natural-language question."""
    q = question.lower()
    ptype = _property_type(q)

    if _mentions(q, "how many", "count", "number of"):
        return f"SELECT count(*) AS count FROM {SALES_MART} WHERE property_type = 'ALL'", "count"

    wants_yield = _mentions(q, "yield", "return on", "roi")
    wants_sales = _mentions(q, "sale", "price", "buy", "purchase")
    wants_rent = _mentions(q, "rent", "rental", "bond")

    if wants_yield:
        return (
            f"SELECT suburb, postcode, property_type, year, median_price, median_rent, "
            f"gross_yield_pct FROM {YIELD_MART} "
            f"WHERE property_type = '{ptype}' AND year = (SELECT max(year) FROM {YIELD_MART}) "
            f"ORDER BY gross_yield_pct DESC LIMIT 10",
            "yield",
        )

    # Rent-only vs sales-only vs both. "Growth suburbs for sales and rent" -> combined.
    if wants_rent and not wants_sales:
        return (
            f"SELECT suburb, postcode, rent_growth_pct, last_median_rent FROM {RENT_MART} "
            f"WHERE property_type = '{ptype}' ORDER BY rent_growth_pct DESC LIMIT 10",
            "rent",
        )
    if wants_sales and not wants_rent:
        return (
            f"SELECT suburb, postcode, sales_growth_pct, last_median_price FROM {SALES_MART} "
            f"WHERE property_type = '{ptype}' ORDER BY sales_growth_pct DESC LIMIT 10",
            "sales",
        )
    return (
        f"SELECT s.suburb, s.postcode, s.sales_growth_pct, r.rent_growth_pct, "
        f"round((s.sales_growth_pct + r.rent_growth_pct) / 2, 1) AS combined_growth_pct "
        f"FROM {SALES_MART} s "
        f"JOIN {RENT_MART} r ON r.postcode = s.postcode AND r.property_type = s.property_type "
        f"WHERE s.property_type = '{ptype}' ORDER BY combined_growth_pct DESC LIMIT 10",
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
        return f"There are {int(rows[0][0]):,} postcodes with sale-price growth data."

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
    # yield: suburb, postcode, property_type, year, median_price, median_rent, gross_yield_pct
    parts = [f"{r[0]} ({_pct_abs(r[6])} gross)" for r in top]
    return "Top areas by gross rental yield — " + "; ".join(parts) + "."
