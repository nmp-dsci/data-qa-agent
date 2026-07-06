"""Vega-Lite chart-spec validation for the make_chart tool.

The model only ever supplies mark/encoding/layer/params/title/width/height —
never `data`. The chart skills (agent/skills/charts.py) splice `data.values` in
from an already-DB-sourced, sandbox-held frame, so a hallucinated number can't
reach the chart even if the model tried.

This module validates the model's half of the spec: an allowlist of top-level
keys, a known-safe set of mark types, layered sub-specs (validated recursively),
and interactive `params` restricted to pan/zoom/brush/legend selections — no
arbitrary Vega expressions (`expr`/`on`/`update`/`views` are rejected).
"""

from __future__ import annotations

from typing import Any

_ALLOWED_TOP_LEVEL_KEYS = {
    "mark",
    "encoding",
    "title",
    "width",
    "height",
    "$schema",
    "layer",
    "params",
}
_ALLOWED_MARKS = {"bar", "line", "point", "arc", "area"}
_ALLOWED_PARAM_SELECT_TYPES = {"interval", "point"}
_ALLOWED_PARAM_BIND = {"scales", "legend"}
_FORBIDDEN_PARAM_KEYS = {"expr", "on", "update", "views"}


class UnsafeChartSpecError(ValueError):
    """Raised when a model-authored chart spec is malformed or out of bounds."""


def _validate_mark_encoding(spec: dict[str, Any]) -> None:
    mark = spec.get("mark")
    mark_type = mark.get("type") if isinstance(mark, dict) else mark
    if mark_type not in _ALLOWED_MARKS:
        raise UnsafeChartSpecError(f"unsupported mark type: {mark_type!r}")
    if "encoding" not in spec or not isinstance(spec["encoding"], dict):
        raise UnsafeChartSpecError("spec missing an 'encoding' object")


def _validate_params(params: Any) -> None:
    if not isinstance(params, list):
        raise UnsafeChartSpecError("params must be a list")
    for p in params:
        if not isinstance(p, dict):
            raise UnsafeChartSpecError("each param must be an object")
        bad = _FORBIDDEN_PARAM_KEYS & set(p)
        if bad:
            raise UnsafeChartSpecError(f"disallowed param keys: {sorted(bad)}")
        select = p.get("select", {})
        sel_type = select.get("type") if isinstance(select, dict) else select
        if sel_type not in _ALLOWED_PARAM_SELECT_TYPES:
            raise UnsafeChartSpecError(f"unsupported param select type: {sel_type!r}")
        bind = p.get("bind")
        if bind is not None and bind not in _ALLOWED_PARAM_BIND:
            raise UnsafeChartSpecError(f"unsupported param bind: {bind!r}")


def trend_overlay_encoding(
    model_enc: dict[str, Any], values: list[dict[str, Any]]
) -> dict[str, Any]:
    """Deterministic encoding for the actual + rolling-average trend overlay.

    Applied server-side by make_chart to any ``compute_trend`` series (rows carry
    a ``layer`` field). Colours each entity (``series``) with one hue and
    separates the two layers by opacity + line weight — faint thin ``actual``
    under a bold solid rolling average — the convention in the answer-templates
    mock. Preserves the model's x/y (axis titles, number formats) and tooltip
    when it supplied them, so authors keep control of the axes, not the styling.
    """
    layer_labels = {r.get("layer") for r in values if isinstance(r, dict)}
    avg_label = next((label for label in layer_labels if label and label != "actual"), "6-mo avg")
    has_series = any(isinstance(r, dict) and "series" in r for r in values)
    enc: dict[str, Any] = {
        "x": model_enc.get("x") or {"field": "month", "type": "temporal", "title": None},
        "y": model_enc.get("y") or {"field": "value", "type": "quantitative"},
        # Distinct lines per entity+layer so the layers don't cross-connect.
        "detail": {"field": "layer", "type": "nominal"},
        "opacity": {
            "field": "layer",
            "type": "nominal",
            "scale": {"domain": ["actual", avg_label], "range": [0.35, 1.0]},
            "legend": None,
        },
        "strokeWidth": {
            "field": "layer",
            "type": "nominal",
            "scale": {"domain": ["actual", avg_label], "range": [1.0, 2.5]},
            "legend": None,
        },
    }
    if has_series:
        enc["color"] = {"field": "series", "type": "nominal", "title": None}
    if "tooltip" in model_enc:
        enc["tooltip"] = model_enc["tooltip"]
    return enc


def validate_chart_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Validate the model-authored portion of a Vega-Lite spec (no `data` key)."""
    if not isinstance(spec, dict):
        raise UnsafeChartSpecError("spec must be an object")

    extra_keys = set(spec) - _ALLOWED_TOP_LEVEL_KEYS
    if extra_keys:
        raise UnsafeChartSpecError(f"disallowed spec keys: {sorted(extra_keys)}")

    if "layer" in spec:
        layers = spec["layer"]
        if not isinstance(layers, list) or not layers:
            raise UnsafeChartSpecError("'layer' must be a non-empty list")
        for sub in layers:
            if not isinstance(sub, dict):
                raise UnsafeChartSpecError("each layer must be an object")
            if set(sub) - _ALLOWED_TOP_LEVEL_KEYS:
                raise UnsafeChartSpecError("layer contains disallowed keys")
            _validate_mark_encoding(sub)
    else:
        _validate_mark_encoding(spec)

    if "params" in spec:
        _validate_params(spec["params"])

    return spec
