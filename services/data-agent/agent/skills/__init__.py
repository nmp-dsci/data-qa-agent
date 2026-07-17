"""Skill library — the tested Python the sandbox preloads (restructure Phase A/C).

The cheap runtime model writes short pandas in the sandbox that *calls these
skills* instead of hand-rolling growth/yield/chart maths. Each skill is authored
and unit-tested offline (by a smart model), so a cheap model inherits
smart-model-quality analysis and a single, consistent presentation standard.

Two cross-cutting mechanics live here:

* ``@skill`` records every skill the model actually calls into ``_USED`` — that
  list is returned per run and logged to ``app.query_runs`` so a wrong answer in
  evals/diagnostics points straight at the skill that produced it.
* ``skill_gap(need, why)`` lets the model flag maths no skill covers yet. We
  start from zero skills, so this is expected early; gaps feed a backlog the
  smart model works through. The model may still answer with inline pandas, but
  it must NOT hand-roll growth/yield maths silently.

Each sandbox run executes in a fresh process, so the module-level ``_USED`` /
``_GAPS`` lists start empty per run; :func:`reset` clears them for in-process
tests.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from typing import Any

import pandas as pd

# Per-run telemetry. A sandbox run is its own (spawned) process, so these are
# fresh each run; reset() is for in-process unit tests that call skills directly.
_USED: list[str] = []
_GAPS: list[dict[str, str]] = []
_INLINE_MATH = [False]
# id() of every DataFrame passed into a skill this run — the signal for "this
# derived frame fed a report object", so the Golden builder's Sandbox view can
# show the enrichment stage (extract → derived frames → objects) and skip the
# scratch frames that never reached a skill. See :func:`capture_frames`.
_CONSUMED: list[int] = []


def reset() -> None:
    _USED.clear()
    _GAPS.clear()
    _CONSUMED.clear()
    _INLINE_MATH[0] = False


def skill[F: Callable[..., Any]](fn: F) -> F:
    """Mark a callable as a skill and record each call for per-run telemetry.

    Also records the id() of any DataFrame argument, so a run knows which derived
    frames actually fed a report object (charts/analysis) vs pure scratch frames.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if fn.__name__ not in _USED:
            _USED.append(fn.__name__)
        for value in (*args, *kwargs.values()):
            if isinstance(value, pd.DataFrame) and id(value) not in _CONSUMED:
                _CONSUMED.append(id(value))
        return fn(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


def capture_frames(
    namespace: dict[str, Any], *, max_frames: int = 8, max_rows: int = 20
) -> list[dict[str, Any]]:
    """Serialise the named derived DataFrames a run built to feed report objects.

    Called after the model code runs, with the sandbox namespace. A frame counts
    as *enrichment* (vs throwaway scratch) when it either fed a skill — so its data
    is in a chart/analysis object (``fed_object``) — OR it added/changed columns vs
    the raw extract, which is how the frames behind the KPI headlines (e.g. a
    per-entity aggregate with a computed ``avg_price``) show up even though their
    figures reach the report as plain scalars. A plain re-slice of the extract that
    no object used is skipped. Frames keep definition order (the compute sequence);
    each is ``{name, columns, rows (head), shape, fed_object}``, JSON-safe (NaN →
    null, numpy/dates coerced) and capped so the payload stays small.
    """
    consumed = set(_CONSUMED)
    extract_cols: list[str] | None = None
    for base in ("df", "extract"):
        base_df = namespace.get(base)
        if isinstance(base_df, pd.DataFrame):
            extract_cols = [str(c) for c in base_df.columns]
            break
    out: list[dict[str, Any]] = []
    for name, value in namespace.items():
        if name.startswith("_") or name in ("df", "extract", "pd", "skills"):
            continue
        if not isinstance(value, pd.DataFrame):
            continue
        cols = [str(c) for c in value.columns]
        fed_object = id(value) in consumed
        # A plain re-slice of the extract that no object used is scratch — skip it.
        if not fed_object and extract_cols is not None and cols == extract_cols:
            continue
        head = value.head(max_rows)
        out.append(
            {
                "name": name,
                "columns": cols,
                "rows": json.loads(head.to_json(orient="values", date_format="iso")),
                "shape": [int(value.shape[0]), int(value.shape[1])],
                "fed_object": fed_object,
            }
        )
        if len(out) >= max_frames:
            break
    return out


def skill_gap(need: str, why: str = "") -> None:
    """Record that no skill covered a piece of maths (bootstrap backlog signal)."""
    _GAPS.append({"need": need, "why": why})


def note_inline_math() -> None:
    """Model calls this when it did risky maths inline — a skill should exist."""
    _INLINE_MATH[0] = True


def used() -> list[str]:
    return list(_USED)


def gaps() -> list[dict[str, str]]:
    return list(_GAPS)


def used_inline_math() -> bool:
    return _INLINE_MATH[0]


# Re-export the skill surface the sandbox exposes as `skills.*`.
from .analysis import (  # noqa: E402
    driver_analysis,
    gross_yield,
    growth_rate,
    latest_value,
    rolling_average,
    top_growth,
    trend_series,
)
from .charts import (  # noqa: E402
    comparison_chart,
    distribution_chart,
    dual_axis_chart,
    profile_chart,
    trend_chart,
)
from .reporting import (  # noqa: E402
    build_insights,
    build_report,
    data_table,
    make_insight,
    related_metrics,
)

__all__ = [
    # analysis
    "trend_series",
    "rolling_average",
    "growth_rate",
    "latest_value",
    "top_growth",
    "gross_yield",
    "driver_analysis",
    # charts
    "trend_chart",
    "comparison_chart",
    "dual_axis_chart",
    "distribution_chart",
    "profile_chart",
    # reporting
    "build_report",
    "build_insights",
    "data_table",
    "make_insight",
    "related_metrics",
    # mechanics
    "skill_gap",
    "note_inline_math",
    "reset",
    "used",
    "gaps",
    "used_inline_math",
    "capture_frames",
]
