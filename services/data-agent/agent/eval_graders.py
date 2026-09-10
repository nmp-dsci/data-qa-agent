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

import re
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


def _scalar_value_col(golden_row: Any, value: str) -> str:
    """The column naming a scalar golden's value: explicit ``value`` wins,
    otherwise a single-column golden row names its own value column."""
    return value or (
        next(iter(golden_row)) if isinstance(golden_row, dict) and len(golden_row) == 1 else ""
    )


def _read_scalar(row: Any, value_col: str) -> Any:
    if value_col and isinstance(row, dict) and value_col in row:
        return row[value_col]
    return _scalar_of(row)


def grade_scalar(golden: Any, actual: Any, *, tolerance_pct: float = 1.0) -> float:
    g, a = _num(golden), _num(actual)
    if g is None or a is None:
        return 1.0 if str(golden).strip() == str(actual).strip() else 0.0
    return 1.0 if within_tolerance(g, a, tolerance_pct) else 0.0


_KPI_STRIP_RE = re.compile(r"[,$%]")
_KPI_NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


def parse_scalar_number(text: Any) -> float | None:
    """Pull the number out of a KPI string (s48 harness fix).

    Strips ``$``, ``,`` and ``%``, then takes the first numeric token and
    applies a trailing k/m/b magnitude suffix if present (``"$718/wk"`` -> 718,
    ``"$1.25m"`` -> 1_250_000, ``"650k"`` -> 650_000). Returns ``None`` for
    anything with no numeric token, rather than guessing.
    """
    if text is None or isinstance(text, bool):
        return None
    if isinstance(text, (int, float)):
        return float(text)
    cleaned = _KPI_STRIP_RE.sub("", str(text))
    match = _KPI_NUM_RE.search(cleaned)
    if not match:
        return None
    value = float(match.group(0))
    tail = cleaned[match.end() : match.end() + 1].lower()
    if tail == "k":
        value *= 1_000
    elif tail == "m":
        value *= 1_000_000
    elif tail == "b":
        value *= 1_000_000_000
    return value


def _manifest_kpi_value(artifact: dict[str, Any] | None) -> float | None:
    """The deck's headline KPI number, when the run produced one (s48).

    A deck can carry more than one KPI slide — the agent sometimes appends a
    "Correction: ..." slide after re-verifying a stale figure (s48 finding) —
    so this takes the *last* non-empty ``kpi`` across the manifest's slides in
    slide order, i.e. whatever the deck says now, not what it said first.
    """
    if not artifact:
        return None
    kpi_text: str | None = None
    for slide in artifact.get("slides") or []:
        if not isinstance(slide, dict):
            continue
        raw = slide.get("kpi")
        if not raw:
            spec = slide.get("spec")
            if isinstance(spec, dict):
                raw = spec.get("kpi")
        if raw:
            kpi_text = str(raw)
    return parse_scalar_number(kpi_text) if kpi_text is not None else None


def _key_match_row(
    golden_rows: Sequence[Any], actual_rows: Sequence[Any], *, value: str = ""
) -> Any | None:
    """The actual row that shares the golden row's identifying columns (s48).

    ``value`` (or, absent that, the golden row's own sole column) is excluded
    from the join keys — otherwise a single-column golden would "key-match" on
    its own value and short-circuit the comparison it exists to make.
    """
    golden_row = golden_rows[0] if golden_rows else None
    if not isinstance(golden_row, dict) or not actual_rows:
        return None
    excluded = {value} if value else (set(golden_row) if len(golden_row) == 1 else set())
    sample = next((r for r in actual_rows if isinstance(r, dict)), None)
    if not sample:
        return None
    candidates = [c for c in golden_row if c not in excluded and c in sample]
    priority = [c for c in candidates if c.lower() in ("month", "period", "date")]
    keys = priority or candidates
    if not keys:
        return None
    for row in actual_rows:
        if isinstance(row, dict) and all(str(row.get(k)) == str(golden_row.get(k)) for k in keys):
            return row
    return None


_SCALAR_REDUCE_MODES = ("manifest_kpi", "key_match", "last_row", "first_row", "max", "min")


def reduce_scalar_actual(
    *,
    golden_rows: Sequence[Any],
    actual_rows: Sequence[Any],
    artifact: dict[str, Any] | None = None,
    value: str = "",
    reduce: str = "",
) -> dict[str, Any]:
    """Pick the one actual value a ``kind: scalar`` golden should be graded
    against (s48 harness fix).

    A scalar question is answered from a full extract (a chart needs the
    series, not one row), so "the first row of the extract" — the previous
    behaviour — is close to arbitrary. Precedence, recorded as
    ``scalar_source``:

    1. ``manifest_kpi`` — the deck's own headline KPI number, when the run
       produced an artifact with one.
    2. ``key_match`` — the actual row sharing the golden row's identifying
       columns (month/period/date, or any other column the golden row carries
       besides its value).
    3. ``last_row`` — extracts are chronological, so the last of >1 rows is
       "latest" (flagged with ``reduced_from``).
    4. ``first_row`` — unchanged behaviour, only when actual has exactly one
       row (or as a last resort when nothing else applies).

    ``reduce`` overrides the precedence with one named mode.
    """
    rows = list(actual_rows)
    golden_row = golden_rows[0] if golden_rows else None
    # The column to read off an actual row. Explicit ``value`` wins; otherwise,
    # a golden row with exactly one column names its own value column, and the
    # same name is what the agent's extract uses for the same mart field. With
    # neither, fall back to "whatever's first" (``_scalar_of``) — the pre-s48
    # behaviour, kept only as a last resort.
    value_col = _scalar_value_col(golden_row, value)

    def _val(row: Any) -> Any:
        return _read_scalar(row, value_col)

    if reduce == "manifest_kpi":
        return {"value": _manifest_kpi_value(artifact), "scalar_source": "manifest_kpi"}
    if reduce == "key_match":
        row = _key_match_row(golden_rows, rows, value=value_col)
        return {"value": _val(row) if row is not None else None, "scalar_source": "key_match"}
    if reduce == "last_row":
        return {
            "value": _val(rows[-1]) if rows else None,
            "scalar_source": "last_row",
            "reduced_from": len(rows),
        }
    if reduce == "first_row":
        return {"value": _val(rows[0]) if rows else None, "scalar_source": "first_row"}
    if reduce in ("max", "min"):
        pairs = [(n, r) for r in rows for n in (_num(_val(r)),) if n is not None]
        if not pairs:
            return {"value": None, "scalar_source": reduce}
        picker = max if reduce == "max" else min
        best = picker(pairs, key=lambda t: t[0])
        return {"value": best[0], "scalar_source": reduce}

    # Auto precedence.
    manifest_val = _manifest_kpi_value(artifact)
    if manifest_val is not None:
        return {"value": manifest_val, "scalar_source": "manifest_kpi"}

    matched = _key_match_row(golden_rows, rows, value=value_col)
    if matched is not None:
        return {"value": _val(matched), "scalar_source": "key_match"}

    if len(rows) > 1:
        return {
            "value": _val(rows[-1]),
            "scalar_source": "last_row",
            "reduced_from": len(rows),
        }
    return {"value": _val(rows[0]) if rows else None, "scalar_source": "first_row"}


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
    artifact: dict[str, Any] | None = None,
    reduce: str = "",
) -> dict[str, Any]:
    """G1 — dispatch on the golden's ``kind``. Grades values, not SQL text.

    ``artifact`` and ``reduce`` only matter for ``kind: scalar`` (s48 harness
    fix) — see ``reduce_scalar_actual`` for what each does.
    """
    if kind == "scalar":
        reduction = reduce_scalar_actual(
            golden_rows=golden_rows,
            actual_rows=actual_rows,
            artifact=artifact,
            value=value,
            reduce=reduce,
        )
        golden_row = golden_rows[0] if golden_rows else None
        golden_value_col = _scalar_value_col(golden_row, value)
        score = grade_scalar(
            _read_scalar(golden_row, golden_value_col),
            reduction["value"],
            tolerance_pct=tolerance_pct,
        )
        result: dict[str, Any] = {"kind": kind, "score": round(score, 4)}
        result["scalar_source"] = reduction["scalar_source"]
        if "reduced_from" in reduction:
            result["reduced_from"] = reduction["reduced_from"]
        return result
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


def grade_artifact(
    artifact: dict[str, Any] | None, *, expect_chart: bool = True, min_slides: int = 1
) -> dict[str, Any]:
    """Grade the deck the user actually received (s46).

    Deliberately asserts **content presence and shape, never layout identity**.
    The agent chooses layouts from the curated catalogue, so a grader that
    checked "slide 2 used Two Charts" would be grading a model decision that is
    free to vary between equally-correct runs — it would flake, and worse, it
    would punish the agent for exercising judgement we asked it to exercise.

    What is genuinely gradeable: the deck exists, it has slides, every slide
    says something in its headline, and a question that needs a chart got one.
    Whether that chart was the prettiest available arrangement is a job for the
    judge or a human, not a deterministic gate.
    """
    issues: list[str] = []
    if not artifact:
        return {"issues": ["no artifact produced"], "passed": False, "slides": 0}

    for key in ("deck_url", "sheet_url"):
        if not artifact.get(key):
            issues.append(f"missing {key}")

    slides = list(artifact.get("slides") or [])
    if len(slides) < min_slides:
        issues.append(f"expected at least {min_slides} slide(s), got {len(slides)}")
    for slide in slides:
        if not str(slide.get("headline") or "").strip():
            issues.append(f"slide {slide.get('index')} has no headline")
    if expect_chart and not any(s.get("has_chart") or s.get("has_table") for s in slides):
        issues.append("no slide carries a chart or a table")

    return {
        "issues": issues,
        "passed": not issues,
        "slides": len(slides),
        # Recorded, not asserted on — useful when reviewing why a deck reads
        # oddly, without turning layout choice into a pass/fail condition.
        "layouts": [str(s.get("layout") or "") for s in slides],
    }
