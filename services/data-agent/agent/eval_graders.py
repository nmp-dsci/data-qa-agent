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


def _normalise_field_name(text: str) -> str:
    """``"Median Weekly Rent"`` and ``"median_weekly_rent"`` -> the same key,
    so a golden's column name can be matched against a KPI slide's human label."""
    return re.sub(r"[^a-z0-9]+", "_", text.strip().casefold()).strip("_")


def _kpi_fields(slide: dict[str, Any]) -> tuple[str, str]:
    """A KPI slide's ``(kpi, kpi_label)`` text, from either the flat manifest
    view or ``spec()`` — whichever the caller happened to pass in."""
    raw_kpi = slide.get("kpi") or ""
    raw_label = slide.get("kpi_label") or ""
    spec = slide.get("spec")
    if isinstance(spec, dict):
        raw_kpi = raw_kpi or spec.get("kpi") or ""
        raw_label = raw_label or spec.get("kpi_label") or ""
    return str(raw_kpi), str(raw_label)


def _manifest_kpi_value(artifact: dict[str, Any] | None, value_col: str = "") -> float | None:
    """The deck's headline KPI number, when the run produced one (s48).

    A deck can carry more than one KPI slide — the agent sometimes appends a
    "Correction: ..." slide after re-verifying a stale figure, or a deck
    answers a multi-metric question with one KPI slide per metric. With a
    golden ``value_col`` to match against, this prefers the slide whose
    ``kpi_label`` names that field (s48 harness fix — grading against
    whichever KPI slide happened to come last silently graded the wrong
    number whenever more than one was present). When a ``value_col`` is
    given and at least one slide carries a ``kpi_label`` but none of them
    names that field, this returns ``None`` rather than guessing — the
    caller falls through to ``key_match``/``last_row`` instead of silently
    grading against an unrelated metric. If no slide carries a label at all,
    there is no basis to disambiguate and the last non-empty ``kpi`` is used,
    as before. Without a ``value_col`` at all, a single unambiguous KPI slide
    is used; more than one is ambiguous and also returns ``None``.
    """
    if not artifact:
        return None
    wanted = _normalise_field_name(value_col) if value_col else ""
    kpi_texts: list[str] = []
    matched_text: str | None = None
    any_labelled = False
    for slide in artifact.get("slides") or []:
        if not isinstance(slide, dict):
            continue
        kpi, label = _kpi_fields(slide)
        if not kpi:
            continue
        kpi_texts.append(kpi)
        if label:
            any_labelled = True
            if wanted and _normalise_field_name(label) == wanted:
                matched_text = kpi
    if wanted and any_labelled:
        chosen = matched_text
    elif wanted:
        chosen = kpi_texts[-1] if kpi_texts else None
    else:
        chosen = kpi_texts[0] if len(kpi_texts) == 1 else None
    return parse_scalar_number(chosen) if chosen is not None else None


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
        return {
            "value": _manifest_kpi_value(artifact, value_col),
            "scalar_source": "manifest_kpi",
        }
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
    manifest_val = _manifest_kpi_value(artifact, value_col)
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


# ---------------------------------------------------------------------------
# Checkpoints (s49 M2, decision D1) — DIAGNOSTIC ONLY.
#
# A golden's outcome (G1 + G5) is what gates. Checkpoints answer the next
# question — *where* did it go wrong — by scoring the three stages the loop
# actually has: the extract, the sandbox analysis, and the deck.
#
# Three rules make them safe to add:
#
# * They never gate. ``scripts/eval_run.py`` computes ``passed`` without them,
#   so a checkpoint that is wrong (or newly added to an old golden) cannot fail
#   a case that answered correctly.
# * They are never shown to the agent. A checkpoint the agent can read is a
#   spec it will satisfy literally — "use skill X" becomes the goal instead of
#   the answer.
# * Every one is optional. An unspecified checkpoint scores ``None``, not 0: a
#   golden with no ``checkpoints`` block must not look like a failing one.
# ---------------------------------------------------------------------------


def _key_tuple(row: Any, key_cols: Sequence[str]) -> tuple[Any, ...] | None:
    """The comparable identity of one row, or None when a key column is absent."""
    if not isinstance(row, dict):
        return None
    out: list[Any] = []
    for col in key_cols:
        if col not in row:
            return None
        value = row[col]
        num = _num(value)
        # Keys arrive as text from one side and numbers from the other (psql vs
        # JSON), so "2077" and 2077 must be the same postcode — but a float key
        # is compared at its numeric value, not its formatting.
        out.append(num if num is not None else str(value))
    return tuple(out)


def checkpoint_sql(
    key_cols: Sequence[str],
    golden_rows: Sequence[Any],
    actual_rows: Sequence[Any],
) -> dict[str, Any]:
    """Did the agent's extract cover the same key tuples as the golden's?

    F1 over the *set* of key tuples: recall catches an extract that filtered too
    hard (missing postcodes, a short window), precision catches one that
    filtered too little. Order and values are G1's job, not this one's.
    """
    want = {t for t in (_key_tuple(r, key_cols) for r in golden_rows) if t is not None}
    got = {t for t in (_key_tuple(r, key_cols) for r in actual_rows) if t is not None}
    if not want:
        return {"key_cols": list(key_cols), "score": None, "reason": "golden produced no key rows"}
    hit = len(want & got)
    precision = hit / len(got) if got else 0.0
    recall = hit / len(want)
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "key_cols": list(key_cols),
        "golden_keys": len(want),
        "actual_keys": len(got),
        "matched": hit,
        "missing": sorted(str(t) for t in list(want - got)[:10]),
        "rows_match": round(f1, 4),
        "score": round(f1, 4),
    }


def checkpoint_analysis(
    expected_skills: Sequence[str],
    derived_cols: Sequence[str],
    *,
    skills_used: Sequence[str] = (),
    frame_columns: Sequence[str] = (),
) -> dict[str, Any]:
    """Did the sandbox use the skills, and produce the columns, the golden expects?

    Two containments, averaged over whichever were specified: expected skills ⊆
    skills the run reported, and derived columns ⊆ columns of the frames the
    analysis step produced. This is the checkpoint the skill miner (s49 M3)
    reads when it looks for a capability the agent kept hand-rolling.
    """
    used = {str(s).strip() for s in skills_used if str(s).strip()}
    cols = {str(c).strip() for c in frame_columns if str(c).strip()}
    parts: list[float] = []
    out: dict[str, Any] = {}
    if expected_skills:
        missing = [s for s in expected_skills if s not in used]
        parts.append(1.0 - len(missing) / len(expected_skills))
        out["expected_skills"] = list(expected_skills)
        out["skills_used"] = sorted(used)
        out["missing_skills"] = missing
    if derived_cols:
        missing_cols = [c for c in derived_cols if c not in cols]
        parts.append(1.0 - len(missing_cols) / len(derived_cols))
        out["derived_cols"] = list(derived_cols)
        out["missing_cols"] = missing_cols
    out["score"] = round(sum(parts) / len(parts), 4) if parts else None
    return out


def _slides_of(manifest: dict[str, Any] | None) -> list[dict[str, Any]]:
    slides = (manifest or {}).get("slides") or []
    return [s for s in slides if isinstance(s, dict)]


def _slide_field(slide: dict[str, Any], field: str) -> str:
    """A slide field, from the flat view or from the ``spec`` it carries."""
    value = slide.get(field)
    if value in (None, ""):
        value = (
            (slide.get("spec") or {}).get(field) if isinstance(slide.get("spec"), dict) else None
        )
    return str(value or "")


def checkpoint_deck(
    layouts_any_of: Sequence[str],
    kpi_label_contains: str,
    manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    """Did the deck use one of the layouts the golden expects, and label its KPI?

    Deliberately *not* part of G5: G5 refuses to assert layout identity because
    the agent's choice is free to vary. This checkpoint records the same thing
    as a diagnosis — a deck that never picks any sensible layout for the shape
    of the question is a presentation problem worth clustering on — precisely
    because it cannot fail the case.
    """
    slides = _slides_of(manifest)
    parts: list[float] = []
    out: dict[str, Any] = {"layouts_used": [_slide_field(s, "layout") for s in slides]}
    if layouts_any_of:
        wanted = {str(x).strip().lower() for x in layouts_any_of}
        hit = any(_slide_field(s, "layout").strip().lower() in wanted for s in slides)
        parts.append(1.0 if hit else 0.0)
        out["layouts_any_of"] = list(layouts_any_of)
        out["layout_hit"] = hit
    if kpi_label_contains:
        needle = kpi_label_contains.strip().lower()
        labels = [_slide_field(s, "kpi_label") for s in slides]
        hit = any(needle in label.lower() for label in labels if label)
        parts.append(1.0 if hit else 0.0)
        out["kpi_label_contains"] = kpi_label_contains
        out["kpi_labels"] = [label for label in labels if label]
        out["kpi_hit"] = hit
    out["score"] = round(sum(parts) / len(parts), 4) if parts else None
    return out


def score_checkpoints(
    spec: dict[str, Any] | None,
    *,
    golden_rows: Sequence[Any] = (),
    actual_rows: Sequence[Any] = (),
    skills_used: Sequence[str] = (),
    frame_columns: Sequence[str] = (),
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """All three checkpoints for one case, as ``{sql, analysis, deck}``.

    Returns ``{}`` for a golden with no ``checkpoints`` block, so the column
    stays empty rather than filling with null scores that read like failures.
    """
    spec = spec or {}
    if not spec:
        return {}
    out: dict[str, Any] = {}
    sql_spec = spec.get("sql") or {}
    if sql_spec.get("key_cols"):
        out["sql"] = checkpoint_sql(list(sql_spec["key_cols"]), golden_rows, actual_rows)
    analysis_spec = spec.get("analysis") or {}
    if analysis_spec.get("expected_skills") or analysis_spec.get("derived_cols"):
        out["analysis"] = checkpoint_analysis(
            list(analysis_spec.get("expected_skills") or []),
            list(analysis_spec.get("derived_cols") or []),
            skills_used=skills_used,
            frame_columns=frame_columns,
        )
    deck_spec = spec.get("deck") or {}
    if deck_spec.get("layouts_any_of") or deck_spec.get("kpi_label_contains"):
        out["deck"] = checkpoint_deck(
            list(deck_spec.get("layouts_any_of") or []),
            str(deck_spec.get("kpi_label_contains") or ""),
            manifest,
        )
    return out


def render_deck_outline(manifest: dict[str, Any] | None, *, max_slides: int = 12) -> str:
    """The deck as one readable block, for the judge's prompt (s49 M2).

    The judge grades what the user received, and what the user received is a
    deck — but a deck is JSON with URLs and object ids in it, most of which is
    noise to a reader. This renders the part a human would look at: layout,
    headline, the KPI and its label, the chart type, the table size. Lives here
    rather than in the runner so the eval and any future online sampler show the
    judge the same shape.
    """
    slides = _slides_of(manifest)
    if not slides:
        return ""
    lines: list[str] = []
    for slide in slides[:max_slides]:
        index = slide.get("index")
        parts = [f"slide {int(index) + 1 if isinstance(index, int) else '?'}"]
        parts.append(_slide_field(slide, "layout") or "unknown layout")
        headline = _slide_field(slide, "headline")
        if headline:
            parts.append(headline)
        kpi, kpi_label = _slide_field(slide, "kpi"), _slide_field(slide, "kpi_label")
        if kpi or kpi_label:
            parts.append(f"kpi {kpi_label or '(unlabelled)'}={kpi or '(blank)'}")
        chart_type = _slide_field(slide, "chart_type")
        if chart_type:
            parts.append(f"chart {chart_type}")
        rows = slide.get("rows") or (slide.get("spec") or {}).get("rows")
        if rows:
            parts.append(f"table {rows} rows")
        lines.append(" · ".join(parts))
    if len(slides) > max_slides:
        lines.append(f"… and {len(slides) - max_slides} more slide(s)")
    return "\n".join(lines)
