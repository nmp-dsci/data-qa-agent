"""Deterministic offline NL->SQL for the NSW property-market growth marts.

The Phase-0/2b stand-in for the LLM: it recognises the growth intents and builds
a governed SELECT (including the sales<->rent JOIN keyed on suburb), then phrases
an answer. When ANTHROPIC_API_KEY is set the Claude agent is used instead, and it
authors the same kind of JOIN from the dbt-manifest schema (see claude_agent.py).
"""

from __future__ import annotations

from typing import Any

from .schema import RENT_MART, SALES_MART

# Combined "growth suburbs" view: rank suburbs by both sale-price and rent growth.
COMBINED_SQL = (
    f"SELECT s.suburb, s.sales_growth_pct, r.rent_growth_pct, "
    f"round((s.sales_growth_pct + r.rent_growth_pct) / 2, 1) AS combined_growth_pct "
    f"FROM {SALES_MART} s JOIN {RENT_MART} r USING (suburb) "
    f"ORDER BY combined_growth_pct DESC LIMIT 10"
)
SALES_ONLY_SQL = (
    f"SELECT suburb, sales_growth_pct, last_median_price "
    f"FROM {SALES_MART} ORDER BY sales_growth_pct DESC LIMIT 10"
)
RENT_ONLY_SQL = (
    f"SELECT suburb, rent_growth_pct, last_median_rent "
    f"FROM {RENT_MART} ORDER BY rent_growth_pct DESC LIMIT 10"
)


def _mentions(q: str, *words: str) -> bool:
    return any(w in q for w in words)


def build_sql(question: str) -> tuple[str, str]:
    """Return (sql, intent) for a natural-language question."""
    q = question.lower()

    wants_sales = _mentions(q, "sale", "price", "buy", "purchase")
    wants_rent = _mentions(q, "rent", "rental", "bond")

    if _mentions(q, "how many", "count", "number of"):
        return f"SELECT count(*) AS count FROM {SALES_MART}", "count"

    # Rent-only vs sales-only vs both. "Growth suburbs for sales and rent" -> combined.
    if wants_rent and not wants_sales:
        return RENT_ONLY_SQL, "rent"
    if wants_sales and not wants_rent:
        return SALES_ONLY_SQL, "sales"
    return COMBINED_SQL, "combined"


def _pct(v: Any) -> str:
    try:
        return f"{float(v):+.1f}%"
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
        return f"There are {int(rows[0][0]):,} suburbs with sale-price growth data."

    top = rows[:5]
    if intent == "combined":
        parts = [f"{r[0]} (sales {_pct(r[1])}, rent {_pct(r[2])})" for r in top]
        return (
            "Top growth suburbs across both markets — " + "; ".join(parts) + ". "
            f"Showing {result['row_count']} suburbs below."
        )
    if intent == "sales":
        parts = [f"{r[0]} ({_pct(r[1])}, now {_money(r[2])})" for r in top]
        return "Top suburbs by sale-price growth — " + "; ".join(parts) + "."
    # rent
    parts = [f"{r[0]} ({_pct(r[1])}, now {_money(r[2])}/wk)" for r in top]
    return "Top suburbs by rent growth — " + "; ".join(parts) + "."
