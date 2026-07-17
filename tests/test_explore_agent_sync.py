"""Cross-service sync — the agent's Explore grounding can't drift from the manifest.

The backend manifest (app/explore/manifest.py) is the source of truth for the
Explore datasets. The data-agent carries a compact mirror (agent/tools_explore.py)
so its schema grounding names the same datasets + backing marts. This test asserts
the two agree, the same guard as the template registry sync test — so agent
capability and the Explore UI can't fall out of step.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "backend-api"))
sys.path.insert(0, str(ROOT / "services" / "data-agent"))

from agent.tools_explore import EXPLORE_DATASETS  # noqa: E402
from app.explore.manifest import MANIFEST  # noqa: E402


def test_agent_and_backend_datasets_match() -> None:
    assert set(EXPLORE_DATASETS) == set(MANIFEST)


def test_agent_backing_tables_match_manifest() -> None:
    for slug, meta in EXPLORE_DATASETS.items():
        assert meta["table"] == MANIFEST[slug].table, slug


def test_agent_headline_metric_is_a_real_metric() -> None:
    for slug, meta in EXPLORE_DATASETS.items():
        assert MANIFEST[slug].metric(meta["headline"]) is not None, slug


def test_agent_geo_key_is_a_real_dimension() -> None:
    for slug, meta in EXPLORE_DATASETS.items():
        assert MANIFEST[slug].dimension(meta["geo"]) is not None, slug


# ---------------------------------------------------------------------------
# Pages contract (s20) — the backend's profile page assembly must produce pages
# the agent-side contract validates, so the two page producers can never fork.
# The one sanctioned difference: ``choropleth`` is an Explore-only object the
# agent may not emit, so it's swapped for a placeholder before validation and
# asserted explicitly instead.
# ---------------------------------------------------------------------------

from app.explore.pages_builder import build_profile_pages  # noqa: E402

PROFILE_PAYLOAD = {
    "metric": "avg_weekly_rent",
    "metric_label": "Avg weekly rent",
    "metric_format": "currency",
    "target_total": 483.0,
    "comparison_total": 448.0,
    "delta": 35.0,
    "delta_pct": 7.81,
    "metric_deltas": [
        {
            "metric": "avg_weekly_rent",
            "label": "Avg weekly rent",
            "fmt": "currency",
            "target": 483.0,
            "comparison": 448.0,
            "delta": 35.0,
            "delta_pct": 7.81,
        },
        {
            "metric": "bond_count",
            "label": "Bonds",
            "fmt": "number",
            "target": 4860.0,
            "comparison": 10110.0,
            "delta": -5250.0,
            "delta_pct": -51.93,
        },
    ],
    "predictors": [
        {
            "predictor": "postcode",
            "label": "Postcode",
            "kind": "categorical",
            "ordinal": False,
            "signal": 62.0,
            "segments": [
                {
                    "value": "2077",
                    "target": 510.0,
                    "comparison": 470.0,
                    "delta": 40.0,
                    "delta_pct": 8.51,
                    "target_n": 120.0,
                },
                {
                    "value": "2076",
                    "target": 480.0,
                    "comparison": 542.0,
                    "delta": -62.0,
                    "delta_pct": -11.44,
                    "target_n": 80.0,
                },
            ],
        },
        {
            "predictor": "property_type",
            "label": "Property type",
            "kind": "categorical",
            "ordinal": False,
            "signal": 55.0,
            "segments": [
                {
                    "value": "house",
                    "target": 640.0,
                    "comparison": 585.0,
                    "delta": 55.0,
                    "delta_pct": 9.4,
                    "target_n": 300.0,
                },
                {
                    "value": "unit",
                    "target": 455.0,
                    "comparison": 430.0,
                    "delta": 25.0,
                    "delta_pct": 5.81,
                    "target_n": 500.0,
                },
            ],
        },
    ],
    "positive_uplifts": [
        {
            "predictor": "property_type",
            "label": "Property type",
            "segment": "house",
            "delta": 55.0,
            "delta_pct": 9.4,
            "target": 640.0,
            "comparison": 585.0,
        },
    ],
    "negative_uplifts": [
        {
            "predictor": "postcode",
            "label": "Postcode",
            "segment": "2076",
            "delta": -62.0,
            "delta_pct": -11.44,
            "target": 480.0,
            "comparison": 542.0,
        },
    ],
    "dataset": "nsw_rent",
    "target_filters": {"year_fy": 2022, "suburb": ["Hornsby"]},
    "comparison_filters": {"year_fy": 2021, "suburb": ["Hornsby"]},
    "geo": {"dimension": "postcode", "layer": "poa_nsw"},
}

DIM_LABELS = {"year_fy": "Financial year", "suburb": "Suburb", "postcode": "Postcode"}


def _built_pages() -> list[dict]:
    return build_profile_pages(dict(PROFILE_PAYLOAD), DIM_LABELS)


def test_profile_pages_validate_through_agent_contract() -> None:
    from agent.pages import PagesEnvelope

    pages = _built_pages()
    assert pages, "profile assembly produced no pages"
    validatable = []
    map_objects = []
    for page in pages:
        clone = {**page, "columns": []}
        for col in page["columns"]:
            kept = []
            for obj in col:
                if obj["type"] == "choropleth":
                    map_objects.append(obj)
                    kept.append(
                        {
                            "type": "text",
                            "element_id": obj["element_id"],
                            "data": {"text": "map placeholder"},
                        }
                    )
                else:
                    kept.append(obj)
            clone["columns"].append(kept)
        validatable.append(clone)
    envelope = PagesEnvelope(pages=validatable)  # raises on any contract break
    assert len(envelope.pages) == len(pages)
    # The map is Explore-only, but its data shape is still pinned here.
    assert map_objects, "profile with geo should carry a map object"
    for obj in map_objects:
        assert {"layer", "key_field", "value_field", "rows"} <= set(obj["data"])


def test_profile_pages_use_cohort_names_and_stable_ids() -> None:
    pages = _built_pages()
    ids = [o["element_id"] for p in pages for col in p["columns"] for o in col]
    assert len(ids) == len(set(ids)), "element ids must be unique"
    assert all(i.startswith("profile:") for i in ids)
    # Cohorts are named by the distinguishing filter (the FY), not Target/Comparison.
    kpi_labels = [
        o["data"]["label"] for p in pages for col in p["columns"] for o in col if o["type"] == "kpi"
    ]
    assert any("2022" in label for label in kpi_labels), kpi_labels


def test_profile_pages_without_geo_skip_the_map() -> None:
    payload = {**PROFILE_PAYLOAD, "geo": None}
    pages = build_profile_pages(payload, DIM_LABELS)
    types = {o["type"] for p in pages for col in p["columns"] for o in col}
    assert "choropleth" not in types
    assert {"kpi", "table", "breakdown"} <= types
