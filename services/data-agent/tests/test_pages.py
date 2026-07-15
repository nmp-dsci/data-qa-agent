"""Pages contract (s07) — deterministic composition + driver analysis.

Unit tests: an InsightReport composes into validated Summary → Insights pages
(element_ids preserved for the feedback/eval loop, chart data lifted from the
validated house specs), and driver_analysis ranks the attribute that most
explains a metric via % contribution.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from agent.pages import (
    PagesEnvelope,
    _insights_headline,
    _one_line,
    _summary_headline,
    chart_object_from_spec,
    compose_insights_page,
    compose_pages,
    compose_summary_page,
    page_plan,
    planned_kinds,
)
from agent.skills import driver_analysis, reset


def _trend_spec() -> dict[str, Any]:
    """The shape agent/skills/charts.py trend_chart emits (validated + data)."""
    return {
        "mark": "line",
        "title": "Median weekly rent",
        "encoding": {
            "x": {"field": "month", "type": "temporal", "title": None},
            "y": {"field": "value", "type": "quantitative"},
            "color": {"field": "series", "type": "nominal", "title": None},
        },
        "data": {
            "values": [
                {"month": "2025-01-01", "value": 640, "series": "2077", "layer": "actual"},
                {"month": "2025-01-01", "value": 645, "series": "2077", "layer": "6-mo avg"},
                {"month": "2025-02-01", "value": 660, "series": "2077", "layer": "actual"},
                {"month": "2025-02-01", "value": 652, "series": "2077", "layer": "6-mo avg"},
            ]
        },
    }


def _bar_spec() -> dict[str, Any]:
    """The shape comparison_chart emits: bar, category x, value y."""
    return {
        "mark": "bar",
        "title": "Median rent by bedrooms",
        "encoding": {
            "x": {"field": "bedroom_band", "type": "nominal"},
            "y": {"field": "median_weekly_rent", "type": "quantitative"},
        },
        "data": {
            "values": [
                {"bedroom_band": "1", "median_weekly_rent": 566},
                {"bedroom_band": "2", "median_weekly_rent": 671},
                {"bedroom_band": "3", "median_weekly_rent": 810},
            ]
        },
    }


def _report() -> dict[str, Any]:
    return {
        "element_id": "report",
        "summary": "Hornsby (2077) 2br unit median rent is $671/wk, up 6.1% YoY.",
        "headlines": [
            {
                "element_id": "headline:0",
                "label": "2br unit median rent",
                "value": "$671/wk",
                "basis": "6-mo rolling, 2026-05",
                "related": False,
                "query_ref": "Q1",
            },
            {
                "element_id": "headline:1",
                "label": "3br unit median rent",
                "value": "$810/wk",
                "basis": "6-mo rolling, 2026-05",
                "related": True,
                "query_ref": "Q1",
            },
        ],
        "insights": [
            {
                "element_id": "insight:0",
                "heading": "3-bed units drove the rise",
                "body": "3br grew +7.3% YoY vs +6.1% for 2br.",
                "query_refs": ["Q1"],
                "chart": _bar_spec(),
            }
        ],
        "profiles": [],
        "main_chart": _trend_spec(),
        "queries": [
            {
                "element_id": "query:Q1",
                "ref": "Q1",
                "purpose": "rent by bedrooms",
                "sql": "SELECT 1",
                "columns": [],
                "rows": [],
                "row_count": 3,
            }
        ],
        "knowledge_pages_used": [],
        "knowledge_version": "abc1234",
    }


def _objects(page: dict[str, Any]) -> list[dict[str, Any]]:
    return [o for col in page["columns"] for o in col]


def test_compose_pages_summary_then_insights() -> None:
    pages, steps = compose_pages(_report(), question="hornsby rent by bedrooms")
    # Only column layouts exist now — both pages render two-col; their semantic
    # role travels as ``kind`` (what the streaming plan gates on).
    assert [p["template"] for p in pages] == ["two-col", "two-col"]
    assert [p["kind"] for p in pages] == ["summary", "insights"]

    # Column model: placement is positional — column 1 = KPIs + note,
    # column 2 = the main chart (height: fill).
    summary = pages[0]
    assert len(summary["columns"]) == 2
    left, right = summary["columns"]
    kinds = [o["type"] for o in _objects(summary)]
    assert kinds.count("kpi") == 1  # only the primary (non-related) headline
    assert "trend" in kinds and "text" in kinds
    # element_ids preserved → pinned feedback keeps working.
    kpi = next(o for o in left if o["type"] == "kpi")
    assert kpi["element_id"] == "headline:0"
    assert kpi["role"] == "headline"
    trend = right[0]
    assert trend["type"] == "trend"
    assert trend["element_id"] == "report:chart"
    assert trend["data"]["intent"] == "line"
    assert trend["data"]["height"] == "fill"
    assert len(trend["data"]["rows"]) == 4

    insights = pages[1]
    breakdown = next(o for o in _objects(insights) if o["type"] == "breakdown")
    assert breakdown["data"]["dimension"] == "bedroom_band"
    assert breakdown["data"]["measure"] == "median_weekly_rent"
    assert breakdown["explains"] == "headline:0"
    note = next(o for o in _objects(insights) if o["type"] == "insight")
    assert note["element_id"] == "insight:0"
    assert note["data"]["refs"] == ["Q1"]

    # The agent now composes a page headline for every report (curators may
    # override): summary → the answer's key takeaway, insights → what explains it.
    assert summary["headline"] == "Hornsby (2077) 2br unit median rent is $671/wk, up 6.1% YoY."
    assert insights["headline"] == "3-bed units drove the rise"

    # Whole output re-validates against the schema (what the frontend consumes).
    PagesEnvelope(pages=pages)  # does not raise

    # Trace records the composition for app.query_runs.
    kinds = [s["kind"] for s in steps]
    assert "object_build" in kinds
    assert "template_pick" in kinds
    assert kinds[-1] == "page_compose"
    assert steps[-1]["status"] == "success"
    assert steps[-1]["templates"] == ["two-col", "two-col"]


def test_page_headline_helpers() -> None:
    # First sentence only, and an abbreviation's dot ("6.1%") is not a break.
    assert _one_line("Rents rose 6.1% YoY. A second sentence.") == "Rents rose 6.1% YoY."
    # Prose with no terminator is used whole; newlines flattened.
    assert _one_line("no terminator\nhere") == "no terminator here"
    # Over-long headlines are truncated with an ellipsis.
    long = "word " * 40
    trimmed = _one_line(long)
    assert trimmed is not None and len(trimmed) <= 120 and trimmed.endswith("…")
    # Empty / whitespace-only report prose → no headline (falls back to None).
    assert _one_line("   ") is None
    assert _summary_headline({"summary": ""}) is None
    assert _insights_headline({"insights": [], "profiles": []}) is None
    # Insights headline prefers the first insight heading, else a profile heading.
    assert _insights_headline({"profiles": [{"heading": "Prices cluster east"}]}) == (
        "Prices cluster east"
    )


def test_compose_pages_empty_report_yields_no_pages() -> None:
    report = {
        "summary": "",
        "headlines": [],
        "insights": [],
        "profiles": [],
        "main_chart": None,
        "queries": [],
    }
    pages, steps = compose_pages(report)
    assert pages == []
    assert steps[-1]["kind"] == "page_compose"


def test_compose_pages_never_raises_on_garbage() -> None:
    pages, steps = compose_pages({"headlines": "not-a-list", "main_chart": 42})
    assert pages == []
    assert steps[-1]["status"] in ("success", "error")


def test_chart_object_grouped_bar_becomes_compare() -> None:
    spec = {
        "mark": "bar",
        "encoding": {
            "x": {"field": "bedroom_band", "type": "nominal"},
            "y": {"field": "median_weekly_rent", "type": "quantitative"},
            "xOffset": {"field": "property_type"},
            "color": {"field": "property_type", "type": "nominal"},
        },
        "data": {
            "values": [
                {"bedroom_band": "2", "median_weekly_rent": 671, "property_type": "unit"},
                {"bedroom_band": "2", "median_weekly_rent": 664, "property_type": "house"},
            ]
        },
    }
    obj = chart_object_from_spec(spec, element_id="x")
    assert obj is not None
    assert obj.type == "compare"
    assert obj.role == "chart"
    assert obj.data["group"] == "property_type"
    assert obj.data["intent"] == "grouped-bar"


def test_dual_axis_chart_series_adds_grouped_layers() -> None:
    """dual_axis_chart(series_col=...) groups the bars + draws a line per series."""
    from agent.skills.charts import dual_axis_chart

    df = pd.DataFrame(
        [
            {"sqm_band": "0-50", "volume": 12, "price": 800, "suburb": "Normanhurst"},
            {"sqm_band": "0-50", "volume": 20, "price": 750, "suburb": "Hornsby"},
            {"sqm_band": "50-100", "volume": 8, "price": 950, "suburb": "Normanhurst"},
        ]
    )
    spec = dual_axis_chart(
        df,
        x_col="sqm_band",
        left_value_col="volume",
        right_value_col="price",
        series_col="suburb",
        x_type="nominal",
    )
    layers = spec["layer"]
    marks = [ly["mark"] if isinstance(ly["mark"], str) else ly["mark"]["type"] for ly in layers]
    assert marks == ["bar", "line"]
    assert layers[0]["encoding"]["color"]["field"] == "suburb"
    assert layers[0]["encoding"]["xOffset"]["field"] == "suburb"
    assert layers[1]["encoding"]["color"]["field"] == "suburb"
    assert spec["resolve"]["scale"]["y"] == "independent"
    assert spec["data"]["values"][0]["sqm_band"] == "0-50"


def test_chart_object_combo_from_dual_axis_spec() -> None:
    """A layered bar+line spec lifts into a compare object carrying BOTH measures
    (bar `measure` + secondary-axis `line_measure`) so the frontend renders the combo."""
    spec = {
        "layer": [
            {
                "mark": "bar",
                "encoding": {
                    "x": {"field": "sqm_band", "type": "nominal"},
                    "y": {"field": "volume", "type": "quantitative"},
                    "color": {"field": "suburb", "type": "nominal"},
                    "xOffset": {"field": "suburb", "type": "nominal"},
                },
            },
            {
                "mark": {"type": "line", "point": True},
                "encoding": {
                    "x": {"field": "sqm_band", "type": "nominal"},
                    "y": {"field": "price", "type": "quantitative"},
                    "color": {"field": "suburb", "type": "nominal"},
                },
            },
        ],
        "resolve": {"scale": {"y": "independent"}},
        "data": {
            "values": [{"sqm_band": "0-50", "volume": 12, "price": 800, "suburb": "Normanhurst"}]
        },
    }
    obj = chart_object_from_spec(spec, element_id="authored:chart", role="chart", height="md")
    assert obj is not None
    assert obj.type == "compare"
    assert obj.data["intent"] == "combo"
    assert obj.data["dimension"] == "sqm_band"
    assert obj.data["measure"] == "volume"
    assert obj.data["line_measure"] == "price"
    assert obj.data["group"] == "suburb"
    assert obj.data["height"] == "md"
    assert len(obj.data["rows"]) == 1


def test_page_rejects_more_columns_than_template_allows() -> None:
    import pytest

    from agent.pages import Page, PageObject

    obj = PageObject(type="text", element_id="t", data={"text": "x"})
    # three-col takes 3 columns; one-col takes 1.
    Page(template="three-col", columns=[[obj], [obj], [obj]])  # ok
    with pytest.raises(ValueError):
        Page(template="one-col", columns=[[obj], [obj]])


def test_page_object_height_validation() -> None:
    import pytest

    from agent.pages import PageObject

    PageObject(type="trend", element_id="c", data={"height": "fill"})  # ok
    PageObject(type="trend", element_id="c", data={"height": 360})  # ok
    with pytest.raises(ValueError):
        PageObject(type="trend", element_id="c", data={"height": "huge"})
    with pytest.raises(ValueError):
        PageObject(type="trend", element_id="c", data={"height": 20})


def test_template_ids_cover_column_limits() -> None:
    from agent.pages import TEMPLATE_COLUMNS, TEMPLATE_IDS

    assert set(TEMPLATE_IDS) == set(TEMPLATE_COLUMNS)
    assert "three-col" in TEMPLATE_IDS
    assert TEMPLATE_COLUMNS["three-col"] == 3


def test_per_page_composers_concat_equals_compose_pages() -> None:
    """Golden parity: the streaming path's per-page composers produce exactly
    the pages the (persisted) compose_pages output contains."""
    report = _report()
    pages, _ = compose_pages(report)
    summary, _ = compose_summary_page(report)
    insights, _ = compose_insights_page(report)
    assert [p for p in (summary, insights) if p is not None] == pages


def test_compose_summary_page_needs_only_pass1_fields() -> None:
    """Page 1 composes from summary/headlines/main_chart alone — the streaming
    path calls it the moment pass 1 lands, before any insights exist."""
    report = _report()
    report["insights"] = []
    page, steps = compose_summary_page(report)
    assert page is not None and page["template"] == "two-col" and page["kind"] == "summary"
    assert [s["kind"] for s in steps] == ["object_build", "template_pick"]
    # And the insights page is honestly absent at that point (chart-less notes).
    ins, _ = compose_insights_page(report)
    assert ins is None


def test_page_plan_gates_by_user_plan() -> None:
    free = page_plan(plan="free")
    assert [(s["kind"], s["status"]) for s in free] == [
        ("summary", "building"),
        ("insights", "locked"),
        ("opportunities", "locked"),
    ]
    assert planned_kinds("free") == ["summary"]

    plus = page_plan(plan="plus")
    assert [(s["kind"], s["status"]) for s in plus] == [
        ("summary", "building"),
        ("insights", "planned"),
        ("opportunities", "locked"),
    ]
    assert [s["index"] for s in plus] == [1, 2, 3]
    assert planned_kinds("plus") == ["summary", "insights"]

    # pro is entitled to opportunities but it isn't buildable yet (M4): the
    # slot is omitted entirely — never a dangling "planned" that can't arrive.
    pro = page_plan(plan="pro")
    assert [(s["kind"], s["status"]) for s in pro] == [
        ("summary", "building"),
        ("insights", "planned"),
    ]
    assert planned_kinds("pro") == ["summary", "insights"]

    # Unknown/missing plans degrade to free — cheapest, least-revealing.
    assert planned_kinds("enterprise-typo") == ["summary"]
    # Every non-locked slot names a real template the frontend registry has —
    # summary/insights kinds now compose with the generic two-col layout.
    for slot in [*free, *plus, *pro]:
        if slot["status"] != "locked":
            assert slot["template"] == "two-col"


def test_driver_analysis_ranks_percent_contribution() -> None:
    reset()
    # bedroom_band separates rent strongly; property_type barely moves it.
    df = pd.DataFrame(
        [
            {"property_type": "unit", "bedroom_band": "1", "total": 566 * 10, "n": 10},
            {"property_type": "unit", "bedroom_band": "3", "total": 810 * 10, "n": 10},
            {"property_type": "house", "bedroom_band": "1", "total": 570 * 10, "n": 10},
            {"property_type": "house", "bedroom_band": "3", "total": 820 * 10, "n": 10},
        ]
    )
    out = driver_analysis(
        df, dimensions=["property_type", "bedroom_band"], value_col="total", den_col="n"
    )
    assert out["top_dimension"] == "bedroom_band"
    ranked = {r["dimension"]: r for r in out["ranked"]}
    assert ranked["bedroom_band"]["score_pct"] > ranked["property_type"]["score_pct"]
    levels = ranked["bedroom_band"]["levels"]
    assert levels[0]["level"] == "3"  # sorted by value desc
    assert levels[0]["delta_pct"] > 0 > levels[-1]["delta_pct"]
    shares = [lv["share_pct"] for lv in levels]
    assert abs(sum(shares) - 100.0) < 0.5


def test_driver_analysis_without_denominator_uses_mean() -> None:
    reset()
    df = pd.DataFrame(
        [
            {"band": "a", "v": 10.0},
            {"band": "a", "v": 12.0},
            {"band": "b", "v": 30.0},
            {"band": "b", "v": 32.0},
        ]
    )
    out = driver_analysis(df, dimensions=["band"], value_col="v")
    assert out["top_dimension"] == "band"
    assert out["overall"] == 21.0
    levels = {lv["level"]: lv for lv in out["ranked"][0]["levels"]}
    assert levels["b"]["value"] == 31.0
    assert levels["a"]["value"] == 11.0
