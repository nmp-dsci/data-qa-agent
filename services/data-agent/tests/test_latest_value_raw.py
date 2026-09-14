"""s49 M5 challenger: ``latest_value`` also reports the unsmoothed latest month."""

from __future__ import annotations

import pandas as pd

from agent import skills


def test_latest_value_reports_raw_month_and_count() -> None:
    df = pd.DataFrame(
        {
            "month": pd.to_datetime(["2026-01-01", "2026-02-01", "2026-03-01"]),
            "rent": [600.0, 700.0, 800.0],
        }
    )
    out = skills.latest_value(df, month_col="month", value_col="rent")
    assert out["month"].startswith("2026-03")
    assert out["raw_value"] == 800.0
    assert out["value"] <= 800.0  # smoothed never exceeds the raw peak here
    assert set(out) == {"value", "month", "raw_value", "count"}
