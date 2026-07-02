from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "data-agent"))

from agent.chart import UnsafeChartSpecError, validate_chart_spec  # noqa: E402


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
