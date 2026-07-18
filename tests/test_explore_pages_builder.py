"""pages_builder.build_profile_pages — payload -> renderable Page objects."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "backend-api"))

from app.explore.pages_builder import build_profile_pages  # noqa: E402

DIM_LABELS = {"property_type": "Property type", "bedroom_band": "Bedrooms", "postcode": "Postcode"}


def _payload(**overrides):
    base = {
        "metric": "avg_weekly_rent",
        "metric_label": "Avg weekly rent",
        "metric_format": "currency",
        "target_total": 560.0,
        "comparison_total": 540.0,
        "delta": 20.0,
        "delta_pct": 3.7,
        "metric_deltas": [],
        "predictors": [
            {
                "predictor": "property_type",
                "label": "Property type",
                "kind": "categorical",
                "ordinal": False,
                "signal": 20.0,
                "segments": [
                    {
                        "value": "house",
                        "target": 560.0,
                        "comparison": 540.0,
                        "delta": 20.0,
                        "delta_pct": 3.7,
                        "target_n": 150,
                    }
                ],
            }
        ],
        "positive_uplifts": [],
        "negative_uplifts": [],
        "dataset": "nsw_rent",
        "target_filters": {"property_type": "house"},
        "comparison_filters": {"property_type": "house"},
        "geo": None,
    }
    base.update(overrides)
    return base


def _breakdown_charts(pages: list[dict]) -> list[dict]:
    out = []
    for page in pages:
        for col in page.get("columns", []):
            for obj in col:
                if obj.get("type") == "breakdown":
                    out.append(obj)
    return out


def test_predictor_chart_carries_sql_when_provided() -> None:
    sql = "select property_type as segment, 'target' as cohort, avg_weekly_rent as value from (...) t"
    payload = _payload(predictor_sql={"property_type": sql})
    pages = build_profile_pages(payload, DIM_LABELS)
    charts = _breakdown_charts(pages)
    assert len(charts) == 1
    assert charts[0]["data"]["sql"] == sql


def test_predictor_chart_has_no_sql_key_when_missing() -> None:
    # No predictor_sql in the payload at all (e.g. an older caller) -> the chart
    # still renders, just without a SQL link, rather than crashing.
    payload = _payload()
    pages = build_profile_pages(payload, DIM_LABELS)
    charts = _breakdown_charts(pages)
    assert len(charts) == 1
    assert "sql" not in charts[0]["data"]
