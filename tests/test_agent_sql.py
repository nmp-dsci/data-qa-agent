from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "data-agent"))

from agent.nl2sql import build_sql  # noqa: E402
from agent.sql_guardrails import UnsafeSQLError, validate_select  # noqa: E402


def test_growth_suburbs_joins_both_marts() -> None:
    sql, intent = build_sql("What are the top growth suburbs for sale price and rent?")
    assert intent == "combined"
    lower = validate_select(sql).lower()
    assert lower.startswith("select")
    assert "marts.mart_sales_growth" in lower
    assert "marts.mart_rent_growth" in lower
    assert "join" in lower and "using (suburb)" in lower


@pytest.mark.parametrize(
    ("question", "expected_intent", "expected_table"),
    [
        ("Which suburbs have the highest rent growth?", "rent", "marts.mart_rent_growth"),
        ("Top suburbs by sale price growth", "sales", "marts.mart_sales_growth"),
        ("How many suburbs do we have?", "count", "marts.mart_sales_growth"),
    ],
)
def test_single_intent_selects(question: str, expected_intent: str, expected_table: str) -> None:
    sql, intent = build_sql(question)
    assert intent == expected_intent
    lower = validate_select(sql).lower()
    assert lower.startswith("select")
    assert expected_table in lower


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM marts.mart_sales_growth",
        "SELECT * FROM marts.mart_sales_growth; DROP TABLE app.users",
        "INSERT INTO app.events VALUES (default)",
    ],
)
def test_guardrail_rejects_write_or_multi_statement_sql(sql: str) -> None:
    with pytest.raises(UnsafeSQLError):
        validate_select(sql)


def test_guardrail_allows_single_select_and_strips_trailing_semicolon() -> None:
    assert validate_select(" SELECT count(*) FROM marts.mart_sales_growth; ") == (
        "SELECT count(*) FROM marts.mart_sales_growth"
    )
