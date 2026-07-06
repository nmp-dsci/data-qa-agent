from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "data-agent"))

from agent.nl2sql import build_sql  # noqa: E402
from agent.sql_guardrails import (  # noqa: E402
    UnsafeSQLError,
    _validate_ast,
    validate_select,
)


def test_growth_suburbs_joins_both_marts_on_postcode() -> None:
    sql, intent = build_sql("What are the top growth suburbs for sale price and rent?")
    assert intent == "combined"
    lower = validate_select(sql).lower()
    assert lower.startswith("with")
    assert "marts.property_sales" in lower
    assert "marts.property_rent" in lower
    assert "join" in lower
    assert "r.postcode = s.postcode" in lower
    # No type mentioned -> aggregate across the mart's native property types.
    assert "property_type = 'all'" not in lower


def test_property_type_filter_detected() -> None:
    sql, intent = build_sql("Top growth suburbs for houses, sale price and rent")
    assert intent == "combined"
    assert "property_type = 'house'" in validate_select(sql).lower()

    sql, _ = build_sql("Top growth suburbs for units")
    assert "property_type = 'unit'" in validate_select(sql).lower()


def test_named_suburb_sale_price_trend_is_not_top_growth() -> None:
    sql, intent = build_sql(
        "show me trend of sale price for houses for Normanhurst vs Hornsby "
        "for all time 2010 to 2026"
    )
    assert intent == "sales_trend"
    lower = validate_select(sql).lower()
    assert "marts.property_sales" in lower
    assert "upper(suburb) in ('normanhurst', 'hornsby')" in lower
    assert "property_type = 'house'" in lower
    assert "month >= date '2010-01-01'" in lower
    assert "month <= date '2026-12-31'" in lower
    assert "order by suburb, month" in lower
    assert "growth_pct" not in lower


def test_yield_question_targets_yield_mart_and_computes_ratio() -> None:
    sql, intent = build_sql("What are the best suburbs for rental yield?")
    assert intent == "yield"
    lower = validate_select(sql).lower()
    assert lower.startswith("with")
    assert "marts.property_sales" in lower
    assert "marts.property_rent" in lower
    # No precomputed gross_yield_pct column in the mart (redesign) — the
    # generated SQL must compute it itself, not just select a column.
    assert "total_weekly_rent" in lower
    assert "total_sale_value" in lower
    assert "gross_yield_pct" in lower
    assert "order by gross_yield_pct desc" in lower


@pytest.mark.parametrize(
    ("question", "expected_intent", "expected_table"),
    [
        ("Which suburbs have the highest rent growth?", "rent", "marts.property_rent"),
        ("Top suburbs by sale price growth", "sales", "marts.property_sales"),
        ("How many suburbs do we have?", "count", "marts.property_sales"),
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
        "DELETE FROM marts.property_sales",
        "SELECT * FROM marts.property_sales; DROP TABLE app.users",
        "INSERT INTO app.events VALUES (default)",
    ],
)
def test_guardrail_rejects_write_or_multi_statement_sql(sql: str) -> None:
    with pytest.raises(UnsafeSQLError):
        validate_select(sql)


def test_guardrail_allows_single_select_and_strips_trailing_semicolon() -> None:
    assert validate_select(" SELECT count(*) FROM marts.property_sales; ") == (
        "SELECT count(*) FROM marts.property_sales"
    )


@pytest.mark.parametrize(
    "sql",
    [
        # CTE-hidden DML — leaves the root a SELECT, so only the AST walk catches it.
        "WITH x AS (DELETE FROM app.users RETURNING id) SELECT * FROM x",
        "WITH x AS (UPDATE app.users SET role = 'admin' RETURNING id) SELECT * FROM x",
        "WITH x AS (INSERT INTO app.events DEFAULT VALUES RETURNING id) SELECT * FROM x",
    ],
)
def test_ast_rejects_cte_hidden_dml(sql: str) -> None:
    # The regex denylist also trips on these, but assert the AST layer rejects
    # them in isolation — it's the real defense against DML the regex might miss.
    with pytest.raises(UnsafeSQLError):
        _validate_ast(sql)


def test_ast_allows_plain_and_cte_selects() -> None:
    # Valid read queries pass the AST check untouched.
    _validate_ast("SELECT count(*) FROM marts.property_sales")
    _validate_ast(
        "WITH g AS (SELECT postcode, suburb FROM staging.int_postcode_geo) "
        "SELECT * FROM g ORDER BY postcode LIMIT 10"
    )
    _validate_ast("SELECT 1 UNION SELECT 2")


def test_ast_rejects_multi_statement() -> None:
    with pytest.raises(UnsafeSQLError):
        _validate_ast("SELECT 1; SELECT 2")


def test_guardrail_ignores_line_and_block_comments() -> None:
    sql = """
    -- This comment mentions DROP TABLE and has a semicolon;
    SELECT count(*) AS n
    FROM marts.property_sales
    /* INSERT, UPDATE and DELETE in comments are ignored too; */
    """
    assert validate_select(sql) == "SELECT count(*) AS n\n    FROM marts.property_sales"
