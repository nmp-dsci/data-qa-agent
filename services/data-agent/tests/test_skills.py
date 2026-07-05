"""Unit tests for the skill library (restructure Phase A).

Pure — no DB, no LLM, no sandbox. Verifies the DataFrame-first skills reproduce
the deterministic analytics maths and the app's report/chart shapes.
"""

from __future__ import annotations

import pandas as pd
import pytest

from agent import skills


@pytest.fixture(autouse=True)
def _reset_skill_telemetry():
    skills.reset()
    yield
    skills.reset()


def _linear_monthly(n: int = 60, start: float = 100.0, step: float = 10.0) -> pd.DataFrame:
    """n monthly points increasing linearly — predictable growth for assertions."""
    months = pd.date_range("2021-01-01", periods=n, freq="MS").strftime("%Y-%m-%d")
    return pd.DataFrame(
        {
            "month": months,
            "avg_price": [start + step * i for i in range(n)],
            "n_sold": [20] * n,
        }
    )


def test_latest_value_is_six_month_smoothed():
    df = _linear_monthly()
    latest = skills.latest_value(df, month_col="month", value_col="avg_price")
    # 6-mo rolling mean of the last six values (640..690) = 665, at the last month.
    assert latest == {"value": 665.0, "month": "2025-12"}


def test_growth_rate_positive_on_increasing_series():
    df = _linear_monthly()
    g3 = skills.growth_rate(df, month_col="month", value_col="avg_price", years=3)
    assert isinstance(g3, float)
    assert g3 > 100.0  # a steeply rising series over 3y


def test_growth_rate_none_without_enough_history():
    df = _linear_monthly(n=12)  # only 1 year
    assert skills.growth_rate(df, month_col="month", value_col="avg_price", years=3) is None


def test_growth_rate_grouped_returns_dict():
    df = _linear_monthly()
    df_a = df.assign(suburb="A")
    df_b = df.assign(suburb="B", avg_price=df["avg_price"] * 2)
    both = pd.concat([df_a, df_b], ignore_index=True)
    out = skills.growth_rate(
        both, month_col="month", value_col="avg_price", years=3, group_col="suburb"
    )
    assert set(out) == {"A", "B"}


def test_trend_series_has_actual_and_rolling_layers():
    df = _linear_monthly()
    series = skills.trend_series(df, month_col="month", value_col="avg_price")
    assert list(series.columns) == ["month", "value", "series", "layer"]
    assert set(series["layer"]) == {"actual", "6-mo avg"}
    assert series["month"].iloc[0].endswith("-01")  # first-of-month datestrings


def test_trend_chart_is_valid_spec_with_inline_data():
    df = _linear_monthly()
    series = skills.trend_series(df, month_col="month", value_col="avg_price")
    spec = skills.trend_chart(series, title="Trend")
    assert spec["mark"] == "line"
    assert spec["title"] == "Trend"
    assert spec["data"]["values"]  # data spliced in server-side style
    assert "encoding" in spec


def test_average_price_via_den_col():
    # value = total_sale_value / n_sold
    df = pd.DataFrame(
        {
            "month": pd.date_range("2024-01-01", periods=6, freq="MS").strftime("%Y-%m-%d"),
            "total_sale_value": [1000, 1200, 1400, 1600, 1800, 2000],
            "n_sold": [10, 10, 10, 10, 10, 10],
        }
    )
    latest = skills.latest_value(
        df, month_col="month", value_col="total_sale_value", den_col="n_sold"
    )
    # 6-mo mean of avg prices (100..200) = 150.
    assert latest["value"] == 150.0


def test_build_report_shape_matches_app():
    df = _linear_monthly()
    series = skills.trend_series(df, month_col="month", value_col="avg_price")
    chart = skills.trend_chart(series)
    insight = skills.make_insight("Prices rising", "Up steadily.", query_refs=["Q1"])
    report = skills.build_report(
        summary="Prices are up.",
        headlines=[{"label": "Latest", "value": "$665", "basis": "6-mo, Dec 2025"}],
        insights=[insight],
        main_chart=chart,
    )
    assert report["element_id"] == "report"
    assert report["summary"] == "Prices are up."
    assert report["headlines"][0]["element_id"] == "headline:0"
    assert report["insights"][0]["element_id"] == "insight:0"
    assert report["main_chart"] == chart


def test_insight_repeating_main_chart_is_not_rendered_twice():
    df = _linear_monthly()
    chart = skills.trend_chart(skills.trend_series(df, month_col="month", value_col="avg_price"))
    insight = skills.make_insight("x", "y", chart=chart)
    report = skills.build_report(summary="s", insights=[insight], main_chart=chart)
    assert report["insights"][0]["chart"] is None


def test_related_metrics_flags_related():
    tiles = skills.related_metrics([{"label": "Yield", "value": "3.2%"}])
    assert tiles[0]["related"] is True


def test_top_growth_ranks_groups_descending():
    # Three suburbs with increasing slopes → B (steepest) should rank first.
    frames = []
    for name, step in [("A", 5.0), ("B", 30.0), ("C", 15.0)]:
        frames.append(_linear_monthly(step=step).assign(suburb=name))
    df = pd.concat(frames, ignore_index=True)
    top = skills.top_growth(
        df, month_col="month", value_col="avg_price", group_col="suburb", years=3, n=2
    )
    assert list(top.columns) == ["suburb", "growth_pct"]
    assert len(top) == 2  # top n
    assert top["suburb"].iloc[0] == "B"  # steepest growth first
    assert top["growth_pct"].iloc[0] >= top["growth_pct"].iloc[1]


def test_gross_yield_annualised_ratio():
    # weekly rent 600 → 31,200/yr on a $2M price = 1.56%.
    rent = pd.DataFrame({"postcode": ["2077"], "month": ["2026-05"], "weekly_rent": [600.0]})
    price = pd.DataFrame({"postcode": ["2077"], "month": ["2026-05"], "price": [2_000_000.0]})
    y = skills.gross_yield(
        rent, price, key_cols=["postcode", "month"], weekly_rent_col="weekly_rent", price_col="price"
    )
    assert y == 1.56


def test_gross_yield_none_without_overlap():
    rent = pd.DataFrame({"postcode": ["2077"], "month": ["2026-05"], "weekly_rent": [600.0]})
    price = pd.DataFrame({"postcode": ["2000"], "month": ["2026-05"], "price": [1_000_000.0]})
    assert (
        skills.gross_yield(
            rent,
            price,
            key_cols=["postcode", "month"],
            weekly_rent_col="weekly_rent",
            price_col="price",
        )
        is None
    )


def test_comparison_chart_is_valid_bar_spec():
    df = pd.DataFrame({"suburb": ["A", "B", "C"], "growth": [10.0, 25.0, 5.0]})
    spec = skills.comparison_chart(df, category_col="suburb", value_col="growth", title="5yr growth")
    assert spec["mark"] == "bar"
    assert spec["title"] == "5yr growth"
    assert spec["encoding"]["x"]["field"] == "suburb"
    assert len(spec["data"]["values"]) == 3


def test_comparison_chart_grouped_adds_series_dimension():
    df = pd.DataFrame(
        {
            "suburb": ["A", "A", "B", "B"],
            "metric": ["sales", "rent", "sales", "rent"],
            "growth": [10.0, 8.0, 25.0, 12.0],
        }
    )
    spec = skills.comparison_chart(
        df, category_col="suburb", value_col="growth", series_col="metric"
    )
    assert "color" in spec["encoding"]
    assert "xOffset" in spec["encoding"]


def test_skill_calls_are_recorded():
    df = _linear_monthly()
    skills.growth_rate(df, month_col="month", value_col="avg_price", years=3)
    skills.latest_value(df, month_col="month", value_col="avg_price")
    used = skills.used()
    assert "growth_rate" in used
    assert "latest_value" in used


def test_skill_gap_recorded():
    skills.skill_gap("seasonality_adjust", "no skill for it")
    assert skills.gaps() == [{"need": "seasonality_adjust", "why": "no skill for it"}]
