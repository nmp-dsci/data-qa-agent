"""Natural-language -> Explore tool state (the offline /explore/ask interpreter)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "backend-api"))

from app.explore import nl_setup  # noqa: E402

GRANTED = {"nsw_sales", "nsw_rent", "nsw_yield"}


def test_profile_fy_comparison_for_houses() -> None:
    state = nl_setup.interpret_profile(
        "give me a comparison between FY2022 and FY2021 for weekly rent on houses", GRANTED
    )
    assert state["dataset"] == "nsw_rent"
    assert state["metric"] == "avg_weekly_rent"
    assert state["target"]["filters"] == {"property_type": "house", "year_fy": 2022}
    assert state["comparison"]["filters"] == {"property_type": "house", "year_fy": 2021}


def test_profile_single_year_defaults_prior_year_comparison() -> None:
    state = nl_setup.interpret_profile("sale prices in 2023", GRANTED)
    assert state["dataset"] == "nsw_sales"
    assert state["target"]["filters"]["year"] == 2023
    assert state["comparison"]["filters"]["year"] == 2022


def test_profile_yield_dataset_inference() -> None:
    state = nl_setup.interpret_profile("compare rental yield FY2022 vs FY2021", GRANTED)
    assert state["dataset"] == "nsw_yield"
    assert state["metric"] == "gross_yield_pct"


def test_trends_two_chart_clauses() -> None:
    state = nl_setup.interpret_trends(
        "show avg rent by bedrooms for houses as a line, and bond volume stacked by postcode",
        GRANTED,
    )
    assert state["dataset"] == "nsw_rent"
    charts = state["charts"]
    assert len(charts) == 2
    assert charts[0]["chart_type"] == "line"
    assert charts[0]["split"] == "bedroom_band"
    assert charts[0]["metric"] == "avg_weekly_rent"
    assert charts[0]["filters"] == {"property_type": "house"}
    assert charts[1]["chart_type"] == "stacked-bar"
    assert charts[1]["split"] == "postcode"
    assert charts[1]["metric"] == "n_rented"


def test_trends_single_chart_fallback() -> None:
    state = nl_setup.interpret_trends("weekly rent trend", GRANTED)
    assert len(state["charts"]) == 1
    assert state["charts"][0]["x"] == "month"


def test_dataset_falls_back_to_granted() -> None:
    # asks about rent but only sales is granted -> falls back to sales
    state = nl_setup.interpret_profile("weekly rent 2022", {"nsw_sales"})
    assert state["dataset"] == "nsw_sales"


def test_forced_dataset_respected() -> None:
    from app.explore.manifest import get_dataset

    state = nl_setup.interpret_profile("prices 2022", GRANTED, dataset=get_dataset("nsw_yield"))
    assert state["dataset"] == "nsw_yield"
