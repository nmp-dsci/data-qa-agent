"""Tests for the quick sandbox runner (restructure Phase A).

Exercises the real spawned-subprocess executor end to end (no DB/LLM): a happy
path that builds a report via skills, plus the safety and telemetry behaviours —
errors surfaced (not raised), the restricted builtins that block file/imports,
skill-gap capture, and the must-assign-`result` contract.
"""

from __future__ import annotations

import pandas as pd

from agent.sandbox import run_code

_TREND_CODE = """
series = skills.trend_series(df, month_col="month", value_col="avg_price")
g = skills.growth_rate(df, month_col="month", value_col="avg_price", years=3)
latest = skills.latest_value(df, month_col="month", value_col="avg_price")
chart = skills.trend_chart(series, title="Avg price trend")
result = skills.build_report(
    summary="Prices rose over the period.",
    headlines=[{"label": "Latest", "value": f"${latest['value']}"}],
    insights=[skills.make_insight("Up", f"Grew {g}% over 3y.")],
    main_chart=chart,
)
"""


def _df(n: int = 60) -> pd.DataFrame:
    months = pd.date_range("2021-01-01", periods=n, freq="MS").strftime("%Y-%m-%d")
    return pd.DataFrame({"month": months, "avg_price": [100.0 + 10 * i for i in range(n)]})


def test_happy_path_builds_report_and_records_skills():
    res = run_code(_TREND_CODE, _df())
    assert res.ok, res.error
    assert res.report is not None
    assert res.report["summary"] == "Prices rose over the period."
    assert res.report["main_chart"]["mark"] == "line"
    # telemetry: the skills the model actually leaned on are recorded per run.
    for name in ("trend_series", "growth_rate", "latest_value", "trend_chart", "build_report"):
        assert name in res.skills_used


def test_model_code_error_is_returned_not_raised():
    res = run_code("result = df.no_such_method()", _df())
    assert not res.ok
    assert res.error is not None
    assert "no_such_method" in res.error or "AttributeError" in res.error


def test_missing_result_is_reported():
    res = run_code("x = 1 + 1", _df())
    assert not res.ok
    assert "result" in (res.error or "")


def test_open_is_blocked_by_restricted_builtins():
    res = run_code("result = open('/etc/passwd').read()", _df())
    assert not res.ok
    # `open` isn't in the sandbox builtins → NameError before any file is touched.
    assert "open" in (res.error or "")


def test_import_os_is_blocked():
    res = run_code("import os\nresult = os.getcwd()", _df())
    assert not res.ok
    assert "import" in (res.error or "").lower() or "__import__" in (res.error or "")


def test_skill_gap_flows_back():
    code = (
        "skills.skill_gap('seasonality_adjust', 'no skill yet')\n"
        "result = skills.build_report(summary='partial')"
    )
    res = run_code(code, _df())
    assert res.ok
    assert [g.need for g in res.skill_gaps] == ["seasonality_adjust"]
