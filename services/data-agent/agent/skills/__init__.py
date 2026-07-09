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
from collections.abc import Callable
from typing import Any

# Per-run telemetry. A sandbox run is its own (spawned) process, so these are
# fresh each run; reset() is for in-process unit tests that call skills directly.
_USED: list[str] = []
_GAPS: list[dict[str, str]] = []
_INLINE_MATH = [False]


def reset() -> None:
    _USED.clear()
    _GAPS.clear()
    _INLINE_MATH[0] = False


def skill[F: Callable[..., Any]](fn: F) -> F:
    """Mark a callable as a skill and record each call for per-run telemetry."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if fn.__name__ not in _USED:
            _USED.append(fn.__name__)
        return fn(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


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
from .reporting import build_insights, build_report, make_insight, related_metrics  # noqa: E402

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
    "make_insight",
    "related_metrics",
    # mechanics
    "skill_gap",
    "note_inline_math",
    "reset",
    "used",
    "gaps",
    "used_inline_math",
]
