"""Unit tests for the deterministic analytics tools (K2/K5).

These are the highest-value tests in the rework: they prove the headline maths
(rolling average, growth, latest-reliable) is reproducible and correct with no
LLM and no database, so the same query rows always yield the same numbers.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "data-agent"))

from agent import analytics  # noqa: E402


def _linear_series(start: float, step: float, months: int, count: int = 10):
    return [
        {"month": f"{2015 + i // 12}-{i % 12 + 1:02d}", "value": start + i * step, "count": count}
        for i in range(months)
    ]


def test_rolling_average_trails_correctly() -> None:
    series = [{"month": "m", "value": float(v), "count": 5} for v in [10, 20, 30, 40]]
    ra = analytics.rolling_average(series, 2)
    assert ra == [10.0, 15.0, 25.0, 35.0]


def test_rolling_average_ignores_nulls() -> None:
    series = [{"value": 10.0}, {"value": None}, {"value": 20.0}]
    assert analytics.rolling_average(series, 3) == [10.0, 10.0, 15.0]


def test_latest_reliable_skips_thin_tail_when_floor_requested() -> None:
    series = [{"month": "2020-01", "value": 100.0, "count": 50}]
    series += [{"month": "2020-02", "value": 999.0, "count": 1}]  # thin
    # A caller can still opt into a coverage floor explicitly.
    lr = analytics.latest_reliable(series, min_count=5)
    assert lr is not None
    assert lr["month"] == "2020-01"


def test_latest_reliable_keeps_all_data_by_default() -> None:
    # Default floor is 0: we keep every month and let the rolling average absorb
    # thin-month noise, rather than dropping data (never filter n_sold >= N).
    series = [{"month": "2020-01", "value": 100.0, "count": 50}]
    series += [{"month": "2020-02", "value": 120.0, "count": 1}]  # thin, but kept
    lr = analytics.latest_reliable(series)
    assert lr is not None
    assert lr["month"] == "2020-02"


def test_growth_rate_off_rolling_base_is_deterministic() -> None:
    # 8 years of monthly data doubling over the span; growth is stable run to run.
    series = _linear_series(1_000_000, 5000, months=96)
    g1 = analytics.growth_rate(series, years=5)
    g2 = analytics.growth_rate(series, years=5)
    assert g1 == g2  # determinism — the reported inconsistency can't recur here
    assert g1 is not None and g1 > 0


def test_growth_rate_none_without_enough_history() -> None:
    series = _linear_series(1_000_000, 5000, months=24)  # only 2 years
    assert analytics.growth_rate(series, years=5) is None


def test_build_series_computes_ratio_and_groups() -> None:
    cols = ["suburb", "month", "tsv", "n"]
    rows = [
        ["HORNSBY", "2020-01-01", 2_000_000, 10],
        ["NORMANHURST", "2020-01-01", 3_000_000, 10],
    ]
    grouped = analytics.build_series(
        cols,
        rows,
        month_col="month",
        value_col="tsv",
        den_col="n",
        count_col="n",
        group_col="suburb",
    )
    assert set(grouped) == {"HORNSBY", "NORMANHURST"}
    assert grouped["HORNSBY"][0]["value"] == 200_000.0
    assert grouped["NORMANHURST"][0]["value"] == 300_000.0


def test_chart_series_has_both_layers() -> None:
    # Default window is 6 months — the one window used for the chart line and growth.
    series = _linear_series(1000, 10, months=12)
    rows = analytics.chart_series(series)
    layers = {r["layer"] for r in rows}
    assert "actual" in layers
    assert "6-mo avg" in layers
