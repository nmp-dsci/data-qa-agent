from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "data-agent"))

from agent.chart import (  # noqa: E402
    UnsafeChartSpecError,
    trend_overlay_encoding,
    validate_chart_spec,
)


def test_allowed_mark_with_encoding_passes() -> None:
    spec = {"mark": "bar", "encoding": {"x": {"field": "suburb"}}}
    assert validate_chart_spec(spec) == spec


def test_mark_object_form_is_accepted() -> None:
    spec = {"mark": {"type": "line"}, "encoding": {"x": {"field": "year"}}}
    assert validate_chart_spec(spec) == spec


def test_unsupported_mark_type_rejected() -> None:
    with pytest.raises(UnsafeChartSpecError):
        validate_chart_spec({"mark": "boxplot", "encoding": {}})


def test_missing_encoding_rejected() -> None:
    with pytest.raises(UnsafeChartSpecError):
        validate_chart_spec({"mark": "bar"})


def test_disallowed_top_level_key_rejected() -> None:
    with pytest.raises(UnsafeChartSpecError):
        validate_chart_spec({"mark": "bar", "encoding": {}, "data": {"values": [{"x": 1}]}})


def test_transform_key_rejected() -> None:
    with pytest.raises(UnsafeChartSpecError):
        validate_chart_spec({"mark": "bar", "encoding": {}, "transform": [{"filter": "true"}]})


def test_non_dict_spec_rejected() -> None:
    with pytest.raises(UnsafeChartSpecError):
        validate_chart_spec("not a dict")  # type: ignore[arg-type]


def _overlay_values() -> list[dict[str, object]]:
    return [
        {"month": "2020-01", "value": 100.0, "series": "HORNSBY", "layer": "actual"},
        {"month": "2020-01", "value": 101.0, "series": "HORNSBY", "layer": "6-mo avg"},
        {"month": "2020-01", "value": 200.0, "series": "NORMANHURST", "layer": "actual"},
    ]


def test_trend_overlay_styling_is_deterministic() -> None:
    enc = trend_overlay_encoding({}, _overlay_values())
    # Colour by entity, and the two layers separated by opacity + line weight.
    assert enc["color"] == {"field": "series", "type": "nominal", "title": None}
    assert enc["detail"] == {"field": "layer", "type": "nominal"}
    assert enc["opacity"]["scale"]["range"] == [0.35, 1.0]  # faint actual, solid avg
    assert enc["strokeWidth"]["scale"]["range"] == [1.0, 2.5]  # thin actual, bold avg
    # The average label is read from the data, so a different window still matches.
    assert enc["opacity"]["scale"]["domain"] == ["actual", "6-mo avg"]


def test_trend_overlay_preserves_model_axes_and_tooltip() -> None:
    model_enc = {
        "y": {"field": "value", "type": "quantitative", "axis": {"format": "$,.0f"}},
        "tooltip": [{"field": "month"}],
    }
    enc = trend_overlay_encoding(model_enc, _overlay_values())
    assert enc["y"]["axis"] == {"format": "$,.0f"}  # author keeps the axis format
    assert enc["tooltip"] == [{"field": "month"}]


def test_trend_overlay_reads_window_label_from_data() -> None:
    values = [
        {"month": "2020-01", "value": 100.0, "series": "A", "layer": "actual"},
        {"month": "2020-01", "value": 101.0, "series": "A", "layer": "3-mo avg"},
    ]
    enc = trend_overlay_encoding({}, values)
    assert enc["opacity"]["scale"]["domain"] == ["actual", "3-mo avg"]
