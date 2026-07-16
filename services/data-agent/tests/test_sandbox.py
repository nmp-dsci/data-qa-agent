"""Tests for the quick sandbox runner (restructure Phase A).

Exercises the real spawned-subprocess executor end to end (no DB/LLM): a happy
path that builds a report via skills, plus the safety and telemetry behaviours —
errors surfaced (not raised), the restricted builtins that block file/imports,
skill-gap capture, and the must-assign-`result` contract.
"""

from __future__ import annotations

import pandas as pd

from agent.sandbox import run_code
from agent.sandbox_agent import _decision_log

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


_FRAMES_CODE = """
# a derived frame that feeds a chart object → captured, fed_object=True
band_agg = df.groupby("area_band", as_index=False)["n_sold"].sum()
band_agg["avg_price"] = band_agg["n_sold"] * 2.0
# a derived frame that feeds a KPI headline via scalars (never passed to a skill)
# → still captured (it added a column vs the extract), fed_object=False
enriched = df.assign(dbl=df["n_sold"] * 2)
# a plain re-slice of the extract that no object uses → scratch, excluded
scratch = df.head(3).copy()
chart = skills.comparison_chart(band_agg, category_col="area_band", value_col="avg_price")
result = skills.build_report(
    summary="ok",
    headlines=[{"label": "peak", "value": f"{enriched['dbl'].max():.0f}"}],
    main_chart=chart,
)
"""


def _band_df() -> pd.DataFrame:
    return pd.DataFrame(
        {"area_band": ["<400", "400-700", "<400", "5000+"], "n_sold": [10.0, 20.0, 5.0, 3.0]}
    )


def test_capture_frames_includes_object_referenced_excludes_scratch():
    """The enrichment stage: named derived frames that feed a chart (fed_object) OR
    added columns vs the extract (e.g. a KPI's aggregate) are captured; a plain
    re-slice of the extract that no object used is dropped (Golden Sandbox view)."""
    res = run_code(_FRAMES_CODE, _band_df())
    assert res.ok, res.error
    by_name = {f["name"]: f for f in res.frames}
    assert by_name["band_agg"]["fed_object"] is True  # in a chart object
    assert by_name["enriched"]["fed_object"] is False  # derived, feeds a KPI scalar
    assert "scratch" not in by_name  # plain slice of the extract → excluded
    assert "df" not in by_name  # the input extract is never a "derived" frame
    assert "avg_price" in by_name["band_agg"]["columns"]  # the calculated column shows
    band = by_name["band_agg"]
    assert band["shape"][1] == len(band["columns"])
    assert isinstance(band["rows"], list) and band["rows"]


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


def test_decision_log_expands_tool_steps_for_eval_assertions() -> None:
    decisions = _decision_log(
        [
            {
                "kind": "knowledge",
                "name": "property-sales-overview",
                "status": "read",
                "why": "sales question",
            },
            {"kind": "schema", "table": "marts.property_sales", "why": "need columns"},
            {
                "kind": "analysis",
                "status": "ok",
                "skills_used": ["trend_series", "trend_chart"],
                "why": "time series chart",
            },
        ]
    )
    assert {
        "type": "table",
        "choice": "marts.property_sales",
        "why": "need columns",
        "order": 2,
    } in decisions
    assert any(d["type"] == "skill" and d["choice"] == "trend_chart" for d in decisions)
    assert any(d["type"] == "chart" and d["choice"] == "trend_chart" for d in decisions)
