"""Unit tests for the period_metric skill (s49 skill-miner)."""

from __future__ import annotations

import pandas as pd
import pytest

from agent.skills.analysis import period_metric


def _totals() -> pd.DataFrame:
    """value_col is a TOTAL (total_weekly_rent) with n_rented as the denominator."""
    return pd.DataFrame(
        [
            # postcode 2250: April 1000/2 = 500
            {"month": "2026-04-01", "postcode": "2250", "total_weekly_rent": 1000.0, "n_rented": 2},
            # postcode 2250: May (2100 + 900) / (3 + 1) = 750  (mean of ratios = 800)
            {"month": "2026-05-01", "postcode": "2250", "total_weekly_rent": 2100.0, "n_rented": 3},
            {"month": "2026-05-01", "postcode": "2250", "total_weekly_rent": 900.0, "n_rented": 1},
            # postcode 2000: May has bonds recorded with a zero denominator
            {"month": "2026-05-01", "postcode": "2000", "total_weekly_rent": 800.0, "n_rented": 0},
        ]
    )


def _rates() -> pd.DataFrame:
    """value_col is already a per-unit RATE (avg_weekly_rent) with a weight."""
    return pd.DataFrame(
        [
            {"month": "2026-05-01", "avg_weekly_rent": 700.0, "n_rented": 3},
            {"month": "2026-05-01", "avg_weekly_rent": 900.0, "n_rented": 1},
        ]
    )


def test_latest_period_is_a_ratio_of_sums_not_a_mean_of_ratios():
    df = _totals()
    out = period_metric(
        df[df["postcode"] == "2250"],
        period_col="month",
        value_col="total_weekly_rent",
        den_col="n_rented",
    )
    assert list(out.columns) == ["month", "value", "numerator", "denominator", "n_rows"]
    assert len(out) == 1
    row = out.iloc[0]
    assert row["month"] == "2026-05"  # the latest period, not every period
    assert row["value"] == pytest.approx(750.0)  # 3000 / 4, NOT (700 + 900) / 2 = 800
    assert row["numerator"] == pytest.approx(3000.0)
    assert row["denominator"] == pytest.approx(4.0)
    assert row["n_rows"] == 2


def test_weight_col_gives_the_same_weighted_answer_as_den_col():
    out = period_metric(
        _rates(), period_col="month", value_col="avg_weekly_rent", weight_col="n_rented"
    )
    assert out.iloc[0]["value"] == pytest.approx(750.0)  # (700*3 + 900*1) / 4
    assert out.iloc[0]["denominator"] == pytest.approx(4.0)


def test_explicit_period_pins_an_earlier_month():
    out = period_metric(
        _totals()[_totals()["postcode"] == "2250"],
        period_col="month",
        value_col="total_weekly_rent",
        den_col="n_rented",
        period="2026-04-01",  # a full date is truncated to its month
    )
    assert out.iloc[0]["month"] == "2026-04"
    assert out.iloc[0]["value"] == pytest.approx(500.0)


def test_unweighted_call_falls_back_to_the_plain_mean():
    out = period_metric(_rates(), period_col="month", value_col="avg_weekly_rent")
    assert out.iloc[0]["value"] == pytest.approx(800.0)  # (700 + 900) / 2
    assert out.iloc[0]["n_rows"] == 2


def test_zero_denominator_group_is_dropped_not_infinite():
    """EDGE: postcode 2000 has rent but zero bonds — NULLIF'd out of the result."""
    out = period_metric(
        _totals(),
        period_col="month",
        value_col="total_weekly_rent",
        den_col="n_rented",
        group_col="postcode",
    )
    assert list(out["postcode"]) == ["2250"]
    assert out.iloc[0]["value"] == pytest.approx(750.0)


def test_thin_group_is_floored_out_by_min_den():
    out = period_metric(
        _totals(),
        period_col="month",
        value_col="total_weekly_rent",
        den_col="n_rented",
        group_col="postcode",
        min_den=10.0,
    )
    assert out.empty
    assert list(out.columns) == ["postcode", "month", "value", "numerator", "denominator", "n_rows"]


def test_per_group_latest_uses_each_groups_own_newest_period():
    df = pd.DataFrame(
        [
            {"month": "2026-05-01", "postcode": "2250", "total_weekly_rent": 3000.0, "n_rented": 4},
            {"month": "2026-04-01", "postcode": "2077", "total_weekly_rent": 1200.0, "n_rented": 2},
        ]
    )
    shared = period_metric(
        df,
        period_col="month",
        value_col="total_weekly_rent",
        den_col="n_rented",
        group_col="postcode",
    )
    assert list(shared["postcode"]) == ["2250"]  # 2077 has no row in the latest month
    each = period_metric(
        df,
        period_col="month",
        value_col="total_weekly_rent",
        den_col="n_rented",
        group_col="postcode",
        per_group_latest=True,
    )
    assert set(each["postcode"]) == {"2250", "2077"}
    assert dict(zip(each["postcode"], each["month"], strict=True)) == {
        "2250": "2026-05",
        "2077": "2026-04",
    }


def test_empty_frame_returns_the_declared_columns():
    out = period_metric(
        pd.DataFrame(columns=["month", "total_weekly_rent", "n_rented"]),
        period_col="month",
        value_col="total_weekly_rent",
        den_col="n_rented",
    )
    assert out.empty
    assert list(out.columns) == ["month", "value", "numerator", "denominator", "n_rows"]


def test_both_weighting_modes_is_a_programming_error():
    with pytest.raises(ValueError):
        period_metric(
            _totals(),
            period_col="month",
            value_col="total_weekly_rent",
            den_col="n_rented",
            weight_col="n_rented",
        )
