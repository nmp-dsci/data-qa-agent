"""Deterministic offline NL->SQL for the housing mart.

This is the Phase-0 stand-in for the LLM. It recognises common analytic intents,
builds a governed SELECT, and phrases a natural-language answer. When
ANTHROPIC_API_KEY is set the Claude agent is used instead (see claude_agent.py).
"""

from __future__ import annotations

import re
from typing import Any

from .schema import PROPERTY_TYPES, SUBURBS

TABLE = "marts.housing"


def _fmt_money(v: Any) -> str:
    try:
        return f"${int(round(float(v))):,}"
    except (TypeError, ValueError):
        return str(v)


def _detect_filters(q: str) -> tuple[str, list[str]]:
    clauses: list[str] = []
    labels: list[str] = []
    for s in SUBURBS:
        if s.lower() in q:
            clauses.append(f"suburb = '{s}'")
            labels.append(s)
            break
    for t in PROPERTY_TYPES:
        if t.lower() in q:
            clauses.append(f"property_type = '{t}'")
            labels.append(t.lower() + "s")
            break
    m = re.search(r"(\d+)\s*[-+ ]?\s*bed", q)
    if m:
        clauses.append(f"bedrooms = {int(m.group(1))}")
        labels.append(f"{m.group(1)}-bedroom")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, labels


def build_sql(question: str) -> tuple[str, str]:
    """Return (sql, intent) for a natural-language question."""
    q = question.lower()
    where, _ = _detect_filters(q)

    if any(k in q for k in ("how many", "count", "number of")):
        return f"SELECT count(*) AS count FROM {TABLE}{where}", "count"

    if any(k in q for k in ("average", "avg", "mean", "typical")):
        if "suburb" in q:
            return (
                f"SELECT suburb, round(avg(price)) AS avg_price, count(*) AS sales "
                f"FROM {TABLE}{where} GROUP BY suburb ORDER BY avg_price DESC",
                "group",
            )
        if "type" in q:
            return (
                f"SELECT property_type, round(avg(price)) AS avg_price, count(*) AS sales "
                f"FROM {TABLE}{where} GROUP BY property_type ORDER BY avg_price DESC",
                "group",
            )
        if "bedroom" in q:
            return (
                f"SELECT bedrooms, round(avg(price)) AS avg_price, count(*) AS sales "
                f"FROM {TABLE}{where} GROUP BY bedrooms ORDER BY bedrooms",
                "group",
            )
        return f"SELECT round(avg(price)) AS avg_price FROM {TABLE}{where}", "scalar"

    if any(k in q for k in ("most expensive", "highest", "top", "priciest", "dearest")):
        return (
            f"SELECT suburb, property_type, price, bedrooms, sale_date "
            f"FROM {TABLE}{where} ORDER BY price DESC LIMIT 5",
            "list",
        )
    if any(k in q for k in ("cheapest", "lowest", "least expensive")):
        return (
            f"SELECT suburb, property_type, price, bedrooms, sale_date "
            f"FROM {TABLE}{where} ORDER BY price ASC LIMIT 5",
            "list",
        )
    if "max" in q or "maximum" in q:
        return f"SELECT max(price) AS max_price FROM {TABLE}{where}", "scalar"
    if "min" in q or "minimum" in q:
        return f"SELECT min(price) AS min_price FROM {TABLE}{where}", "scalar"

    return (
        f"SELECT suburb, property_type, price, bedrooms, bathrooms, sale_date "
        f"FROM {TABLE}{where} ORDER BY sale_date DESC LIMIT 10",
        "list",
    )


def phrase_answer(question: str, intent: str, result: dict[str, Any]) -> str:
    rows: list[list[Any]] = result["rows"]
    cols: list[str] = result["columns"]
    if result["row_count"] == 0:
        return (
            "No rows are visible to you for that question — your account may not "
            "have access to this dataset. (Row-Level Security returned 0 rows.)"
        )

    if intent == "count":
        return f"There are {int(rows[0][0]):,} matching properties."

    if intent == "scalar":
        label = cols[0].replace("_", " ")
        return f"The {label} is {_fmt_money(rows[0][0])}."

    if intent == "group":
        key = cols[0]
        top = rows[:5]
        parts = [f"{r[0]}: {_fmt_money(r[1])} ({r[2]} sales)" for r in top]
        return f"Average price by {key} (highest first) — " + "; ".join(parts) + "."

    # list
    lead = rows[0]
    return (
        f"Top result: a {lead[1]} in {lead[0]} at {_fmt_money(lead[2])} "
        f"({lead[3]} bed). Showing {result['row_count']} rows below."
    )
