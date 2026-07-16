"""Deterministic graders for the eval loop (s14 E2).

Pure comparison logic — no DB, no network — so it unit-tests cheaply and the
eval runner shares one implementation with the PR pack-lint. Maps to the three
answer stages:

* G1 — extraction: grade the extracted *values* against the golden SQL's values
  (any query path is fine — numbers, not SQL text).
* G2 — preparation: grade the sandbox-produced metrics against the golden prep
  (reuses the same value comparators).
* G3 — presentation (deterministic half): grade the delivered report/pages shape,
  reusing the agent's own ``report_structural_issues`` so evals check exactly what
  the app lints. The LLM insight half of G3 is the judge, not here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .report import report_structural_issues


def _num(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def within_tolerance(golden: float, actual: float, tolerance_pct: float) -> bool:
    """Relative tolerance; falls back to absolute when the golden is zero."""
    if golden == 0:
        return abs(actual) <= tolerance_pct / 100.0
    return abs(actual - golden) / abs(golden) <= tolerance_pct / 100.0


def _scalar_of(row: Any) -> Any:
    """First value of a row — whether a dict, a sequence, or a bare scalar."""
    if isinstance(row, dict):
        return next(iter(row.values()), None)
    if isinstance(row, (list, tuple)):
        return row[0] if row else None
    return row


def grade_scalar(golden: Any, actual: Any, *, tolerance_pct: float = 1.0) -> float:
    g, a = _num(golden), _num(actual)
    if g is None or a is None:
        return 1.0 if str(golden).strip() == str(actual).strip() else 0.0
    return 1.0 if within_tolerance(g, a, tolerance_pct) else 0.0


def _key_values(rows: Sequence[Any], key: str) -> list[Any]:
    return [r.get(key) for r in rows if isinstance(r, dict) and key in r]


def grade_row_set(golden: Sequence[Any], actual: Sequence[Any], *, key: str) -> float:
    """F1 over the set of key-column values (order-insensitive)."""
    g = set(_key_values(golden, key))
    a = set(_key_values(actual, key))
    if not g and not a:
        return 1.0
    tp = len(g & a)
    if tp == 0:
        return 0.0
    precision = tp / len(a)
    recall = tp / len(g)
    return 2 * precision * recall / (precision + recall)


def grade_ranked_set(
    golden: Sequence[Any], actual: Sequence[Any], *, key: str, k: int = 5
) -> float:
    """Top-k overlap: fraction of the golden's top-k keys present in the agent's top-k."""
    g = _key_values(golden, key)[:k]
    a = set(_key_values(actual, key)[:k])
    if not g:
        return 1.0
    return sum(1 for x in g if x in a) / len(g)


def grade_series(
    golden: Sequence[Any],
    actual: Sequence[Any],
    *,
    key: str,
    value: str,
    tolerance_pct: float = 1.0,
) -> float:
    """Per-point tolerance on keys present in both → fraction of golden points matched."""
    a_map = {r.get(key): r.get(value) for r in actual if isinstance(r, dict)}
    points = [(r.get(key), r.get(value)) for r in golden if isinstance(r, dict)]
    if not points:
        return 1.0
    ok = 0
    for k_, gval in points:
        if k_ in a_map:
            gn, an = _num(gval), _num(a_map[k_])
            if gn is not None and an is not None and within_tolerance(gn, an, tolerance_pct):
                ok += 1
    return ok / len(points)


def grade_extraction(
    *,
    kind: str,
    golden_rows: Sequence[Any],
    actual_rows: Sequence[Any],
    key: str = "",
    value: str = "",
    k: int = 5,
    tolerance_pct: float = 1.0,
) -> dict[str, Any]:
    """G1 — dispatch on the golden's ``kind``. Grades values, not SQL text."""
    if kind == "scalar":
        score = grade_scalar(
            _scalar_of(golden_rows[0] if golden_rows else None),
            _scalar_of(actual_rows[0] if actual_rows else None),
            tolerance_pct=tolerance_pct,
        )
    elif kind == "row_set":
        score = grade_row_set(golden_rows, actual_rows, key=key)
    elif kind == "ranked_set":
        score = grade_ranked_set(golden_rows, actual_rows, key=key, k=k)
    elif kind == "series":
        score = grade_series(
            golden_rows, actual_rows, key=key, value=value, tolerance_pct=tolerance_pct
        )
    else:
        return {"kind": kind, "score": 0.0, "error": f"unknown golden kind: {kind}"}
    return {"kind": kind, "score": round(score, 4)}


def _object_types(report: dict[str, Any] | None) -> set[str]:
    """Object types present across a report's pages (columns[i][j].type)."""
    types: set[str] = set()
    if not report:
        return types
    for page in report.get("pages", []) or []:
        for col in page.get("columns", []) or []:
            for obj in col or []:
                if isinstance(obj, dict) and obj.get("type"):
                    types.add(str(obj["type"]))
    return types


def grade_presentation_format(
    report: dict[str, Any] | None, *, expected_objects: Sequence[str] = ()
) -> dict[str, Any]:
    """Deterministic half of G3 — structural issues + expected object types present.

    Reuses ``report_structural_issues`` so the eval grades exactly what the app
    lints; ``expected_objects`` are page object types the question implies (e.g.
    ``trend``), each missing one recorded as an issue.
    """
    issues: list[str] = list(report_structural_issues(report or {}))
    present = _object_types(report)
    issues.extend(
        f"missing expected object: {want}" for want in expected_objects if want not in present
    )
    return {"issues": issues, "passed": not issues, "object_types": sorted(present)}
