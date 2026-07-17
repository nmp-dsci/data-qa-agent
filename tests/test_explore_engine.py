"""Profile engine (pure port of profileCalc.js / profileMetrics.js)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "backend-api"))

from app.explore import engine  # noqa: E402
from app.explore.manifest import get_dataset  # noqa: E402


def _rent():
    ds = get_dataset("nsw_rent")
    assert ds is not None
    return ds


def _profile():
    ds = _rent()
    target_totals = {"n_rented": 4860, "total_weekly_rent": 2_347_380, "avg_weekly_rent": 483}
    comparison_totals = {"n_rented": 10110, "total_weekly_rent": 4_529_280, "avg_weekly_rent": 448}
    target_by = {
        "bedroom_band": [
            {"segment": "2", "avg_weekly_rent": 470, "_n": 100},
            {"segment": "3", "avg_weekly_rent": 538, "_n": 200},
            {"segment": "4", "avg_weekly_rent": 641, "_n": 60},
            {"segment": "0", "avg_weekly_rent": 400, "_n": 1},  # thin -> dropped
        ],
        "property_type": [
            {"segment": "house", "avg_weekly_rent": 560, "_n": 150},
            {"segment": "unit", "avg_weekly_rent": 419, "_n": 300},
        ],
    }
    comparison_by = {
        "bedroom_band": [
            {"segment": "2", "avg_weekly_rent": 452, "_n": 220},
            {"segment": "3", "avg_weekly_rent": 505, "_n": 300},
            {"segment": "4", "avg_weekly_rent": 599, "_n": 90},
            {"segment": "0", "avg_weekly_rent": 402, "_n": 5},
        ],
        "property_type": [
            {"segment": "house", "avg_weekly_rent": 540, "_n": 200},
            {"segment": "unit", "avg_weekly_rent": 448, "_n": 500},
        ],
    }
    return engine.build_profile(
        ds, "avg_weekly_rent", target_totals, comparison_totals, target_by, comparison_by
    )


def test_topline_delta() -> None:
    r = _profile()
    assert r.target_total == 483
    assert r.comparison_total == 448
    assert r.delta == 35.0
    assert r.delta_pct == 7.81  # 35/448*100


def test_all_metric_deltas_present() -> None:
    r = _profile()
    names = {d.metric for d in r.metric_deltas}
    assert names == {"n_rented", "total_weekly_rent", "avg_weekly_rent"}
    n = next(d for d in r.metric_deltas if d.metric == "n_rented")
    assert n.delta == -5250 and n.target == 4860 and n.comparison == 10110


def test_predictors_ordered_by_signal() -> None:
    r = _profile()
    # bedroom_band's strongest move is 4br (+42), property_type's is unit (-29);
    # bedrooms therefore leads.
    assert [p.predictor for p in r.predictors] == ["bedroom_band", "property_type"]
    beds = r.predictors[0]
    assert beds.signal == 42.0


def test_ordinal_predictor_keeps_natural_order_and_drops_thin_segment() -> None:
    r = _profile()
    beds = next(p for p in r.predictors if p.predictor == "bedroom_band")
    # '0' was thin (_n=1) so it's dropped; remaining kept in numeric order.
    assert [s.value for s in beds.segments] == ["2", "3", "4"]


def test_categorical_predictor_sorted_by_delta() -> None:
    r = _profile()
    pt = next(p for p in r.predictors if p.predictor == "property_type")
    # house +20 before unit -29
    assert [s.value for s in pt.segments] == ["house", "unit"]
    assert pt.segments[0].delta == 20.0 and pt.segments[1].delta == -29.0


def test_positive_and_negative_leaderboards() -> None:
    r = _profile()
    assert r.positive_uplifts[0]["segment"] == "4"  # +42 strongest positive
    assert r.positive_uplifts[0]["delta"] == 42.0
    assert r.negative_uplifts[0]["segment"] == "unit"  # -29 strongest negative
    assert r.negative_uplifts[0]["delta"] == -29.0


def test_to_public_shape() -> None:
    r = _profile().to_public()
    assert r["metric"] == "avg_weekly_rent"
    assert r["metric_format"] == "currency"
    assert isinstance(r["predictors"], list) and r["predictors"][0]["segments"]
