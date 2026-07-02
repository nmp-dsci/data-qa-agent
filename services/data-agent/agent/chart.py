"""Vega-Lite chart-spec validation for the make_chart tool.

The model only ever supplies mark/encoding/title/width/height — never `data`.
make_chart() (in llm_agent.py) splices `data.values` in server-side from the
already-DB-sourced run_sql result, so a hallucinated number can't reach the
chart even if the model tried. This module just validates the model's half of
the spec: an allowlist of top-level keys and a known-safe set of mark types.
No heavy imports here on purpose — stays importable from the root test venv.
"""

from __future__ import annotations

from typing import Any

_ALLOWED_TOP_LEVEL_KEYS = {"mark", "encoding", "title", "width", "height", "$schema"}
_ALLOWED_MARKS = {"bar", "line", "point", "arc", "area"}


class UnsafeChartSpecError(ValueError):
    """Raised when a model-authored chart spec is malformed or out of bounds."""


def validate_chart_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Validate the model-authored portion of a Vega-Lite spec (no `data` key)."""
    if not isinstance(spec, dict):
        raise UnsafeChartSpecError("spec must be an object")

    extra_keys = set(spec) - _ALLOWED_TOP_LEVEL_KEYS
    if extra_keys:
        raise UnsafeChartSpecError(f"disallowed spec keys: {sorted(extra_keys)}")

    mark = spec.get("mark")
    mark_type = mark.get("type") if isinstance(mark, dict) else mark
    if mark_type not in _ALLOWED_MARKS:
        raise UnsafeChartSpecError(f"unsupported mark type: {mark_type!r}")

    if "encoding" not in spec or not isinstance(spec["encoding"], dict):
        raise UnsafeChartSpecError("spec missing an 'encoding' object")

    return spec
