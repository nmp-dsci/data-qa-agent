from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "data-agent"))

from agent.nl2sql import build_sql  # noqa: E402
from agent.sql_guardrails import UnsafeSQLError, validate_select  # noqa: E402


def test_growth_suburbs_joins_both_marts_on_postcode() -> None:
    sql, intent = build_sql("What are the top growth suburbs for sale price and rent?")
    assert intent == "combined"
    lower = validate_select(sql).lower()
    assert lower.startswith("with")
    assert "marts.mart_sales_summary" in lower
    assert "marts.mart_rent_summary" in lower
    assert "join" in lower
    assert "r.postcode = s.postcode" in lower
    # No type mentioned -> blended 'ALL' row, not a specific type.
    assert "property_type = 'all'" in lower


def test_property_type_filter_detected() -> None:
    sql, intent = build_sql("Top growth suburbs for houses, sale price and rent")
    assert intent == "combined"
    assert "property_type = 'house'" in validate_select(sql).lower()

    sql, _ = build_sql("Top growth suburbs for units")
    assert "property_type = 'unit'" in validate_select(sql).lower()


def test_yield_question_targets_yield_mart_and_computes_ratio() -> None:
    sql, intent = build_sql("What are the best suburbs for rental yield?")
    assert intent == "yield"
    lower = validate_select(sql).lower()
    assert lower.startswith("select")
    assert "marts.mart_property_yield" in lower
    # No precomputed gross_yield_pct column in the mart (redesign) — the
    # generated SQL must compute it itself, not just select a column.
    assert "median_rent * 52 / median_price" in lower
    assert "gross_yield_pct" in lower
    assert "order by gross_yield_pct desc" in lower


@pytest.mark.parametrize(
    ("question", "expected_intent", "expected_table"),
    [
        ("Which suburbs have the highest rent growth?", "rent", "marts.mart_rent_summary"),
        ("Top suburbs by sale price growth", "sales", "marts.mart_sales_summary"),
        ("How many suburbs do we have?", "count", "marts.mart_sales_summary"),
    ],
)
def test_single_intent_selects(question: str, expected_intent: str, expected_table: str) -> None:
    sql, intent = build_sql(question)
    assert intent == expected_intent
    lower = validate_select(sql).lower()
    assert expected_table in lower


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM marts.mart_sales_summary",
        "SELECT * FROM marts.mart_sales_summary; DROP TABLE app.users",
        "INSERT INTO app.events VALUES (default)",
    ],
)
def test_guardrail_rejects_write_or_multi_statement_sql(sql: str) -> None:
    with pytest.raises(UnsafeSQLError):
        validate_select(sql)


def test_guardrail_allows_single_select_and_strips_trailing_semicolon() -> None:
    assert validate_select(" SELECT count(*) FROM marts.mart_sales_summary; ") == (
        "SELECT count(*) FROM marts.mart_sales_summary"
    )
