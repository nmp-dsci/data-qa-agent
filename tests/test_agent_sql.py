from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "data-agent"))

from agent.nl2sql import build_sql  # noqa: E402
from agent.sql_guardrails import UnsafeSQLError, validate_select  # noqa: E402


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("What is the average sale price by suburb?", "GROUP BY suburb"),
        ("How many properties are there?", "count(*)"),
        ("What are the 5 most expensive properties?", "ORDER BY price DESC"),
    ],
)
def test_stub_generates_selects_for_core_intents(question: str, expected: str) -> None:
    sql, _intent = build_sql(question)

    assert validate_select(sql).lower().startswith("select")
    assert expected.lower() in sql.lower()


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM marts.housing",
        "SELECT * FROM marts.housing; DROP TABLE app.users",
        "INSERT INTO app.events VALUES (default)",
    ],
)
def test_guardrail_rejects_write_or_multi_statement_sql(sql: str) -> None:
    with pytest.raises(UnsafeSQLError):
        validate_select(sql)


def test_guardrail_allows_single_select_and_strips_trailing_semicolon() -> None:
    assert validate_select(" SELECT count(*) FROM marts.housing; ") == (
        "SELECT count(*) FROM marts.housing"
    )
