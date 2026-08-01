"""Deterministic Presentation Object builder (s18 Golden Sandbox).

The Golden Sandbox's *Presentation Object builder* lets a curator create a named
visualisation dataset by picking columns + skills — no LLM, so it is repeatable
(and testable). Given a structured ``spec`` (grain, dimension, group, the bar/line
measures + their windows) this module deterministically emits:

* a **canonical extract SQL** at the object's finest grain — used to *extend the
  shared golden extract* so every column the object needs (e.g. ``area_band``,
  ``total_sale_value``) is present, carrying the suburb / property_type filters
  parsed from the golden's current extract; and
* a self-contained **run_analysis snippet** (``build_object_code``) that windows +
  aggregates that extract to the chart grain and calls a house chart skill,
  ending in ``result = skills.build_report(main_chart=<chart>)``.

The snippet runs in the SAME governed sandbox as every other run_analysis, and the
produced ``main_chart`` is lifted back into a page object by
:func:`agent.pages.chart_object_from_spec` with the object's stable
``element_id`` (``obj:<slug(name)>``), so the report can link to it by name.

Only additive source columns are aggregated (``n_sold``, ``total_sale_value``);
non-additive figures like ``avg_sale_price`` are always recomposed as
``sum(total_sale_value)/sum(n_sold)`` so re-aggregation stays correct.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .schema import RENT_MART, SALES_MART, YIELD_MART
from .units import unit_for_measure

# ---------------------------------------------------------------------------
# Mart profiles (s22 P2) — the deterministic builder is dataset-aware. Each
# profile names the source table, its additive legs (a count + a value that may
# be summed across a window), the recomposed ratio (a non-additive average
# recomputed as sum(value)/sum(count)), a sensible default grain, and the
# equality/IN filters carried verbatim from the golden's current extract.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MartProfile:
    table: str
    count_col: str  # additive count (bar default), e.g. n_sold / n_rented
    value_col: str  # additive total, e.g. total_sale_value / total_weekly_rent
    ratio_col: str  # recomposed average (line default), e.g. avg_sale_price
    default_grain: tuple[str, ...]

    @property
    def additive(self) -> tuple[str, str]:
        return (self.count_col, self.value_col)


_PROFILES: dict[str, MartProfile] = {
    "nsw_sales": MartProfile(
        table=SALES_MART,
        count_col="n_sold",
        value_col="total_sale_value",
        ratio_col="avg_sale_price",
        default_grain=("suburb", "area_band", "month"),
    ),
    "nsw_rent": MartProfile(
        table=RENT_MART,
        count_col="n_rented",
        value_col="total_weekly_rent",
        ratio_col="avg_weekly_rent",
        default_grain=("postcode", "bedroom_band", "month"),
    ),
    # The yield mart is sales ⨝ rent at postcode/type/month — it has no suburb or
    # band columns, so without its own profile the builder fell back to sales and
    # emitted SQL over columns property_yield doesn't have. The additive legs
    # recompose avg_weekly_rent (= Σweekly_rent / Σrented); gross_yield_pct the
    # structured builder cannot produce at all: no single num/den pair recomposes
    # it (it needs ×52×100 scaling and both count legs), and the wavg .round()
    # would floor the raw Σrent/Σvalue ratio to 0 — a curator wanting the yield
    # line must use the NL/sandbox path, not a num/den line_measure.
    "nsw_yield": MartProfile(
        table=YIELD_MART,
        count_col="n_rented",
        value_col="total_weekly_rent",
        ratio_col="avg_weekly_rent",
        default_grain=("postcode", "property_type", "month"),
    ),
}


def profile_for(dataset: str | None) -> MartProfile:
    """The mart profile for a dataset slug (defaults to sales — the historical
    behaviour, so callers that don't pass a dataset are unchanged)."""
    return _PROFILES.get(dataset or "nsw_sales", _PROFILES["nsw_sales"])


_STOPWORDS = frozenset(
    "a an the of by for with and to as only that is in on per over this these those "
    "chart show me plot graph across between into vs versus".split()
)


def name_from_instruction(instruction: str, *, max_words: int = 5) -> str:
    """A short, stable slug derived from the salient words of an NL instruction —
    used to give an unnamed object (s22 NL path) a linkable ``obj:<slug>`` id."""
    words = re.sub(r"[^a-z0-9\s]", " ", (instruction or "").lower()).split()
    kept = [w for w in words if w not in _STOPWORDS][:max_words]
    return slug(" ".join(kept)) if kept else "object"


# ---------------------------------------------------------------------------
# Identity — a stable, link-able element_id per named object.
# ---------------------------------------------------------------------------


def slug(name: str) -> str:
    """Kebab-case slug of a human object name (``Line Bar!`` → ``line-bar``)."""
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return s or "object"


def element_id_for(name: str) -> str:
    """The stable element_id a named presentation object links by."""
    return f"obj:{slug(name)}"


# ---------------------------------------------------------------------------
# The spec — the structured form state the builder collects (all optional with
# sensible house defaults so a partial form still yields runnable code).
# ---------------------------------------------------------------------------


# A measure is a BASE aggregation (sum / mean / weighted-avg) plus an optional
# DERIVE that augments it over the window. Aliases map old/free-text values onto
# the canonical derive so saved goldens (which stored ``how``) keep working.
_DERIVE_ALIASES = {
    "share": "share",
    "pct": "share",
    "percent": "share",
    "% share": "share",
    "% of total": "share",
    "share_of_x": "share",
    "pct_of_x": "share",
    "mix": "share",
    "growth": "growth",
    "growth %": "growth",
    "growth_pct": "growth",
    "delta": "growth",
    "delta %": "growth",
    "growth % (period-over-period)": "growth",
    "latest": "latest",
    "latest value": "latest",
    "current": "latest",
    "rolling": "rolling",
    "rolling average": "rolling",
    "rolling_avg": "rolling",
    "moving average": "rolling",
    "moving_avg": "rolling",
    "index": "index",
    "index to 100": "index",
    "index=100": "index",
    "indexed": "index",
    "cumulative": "cumulative",
    "running total": "cumulative",
    "cumsum": "cumulative",
    "rank": "rank",
    "ranking": "rank",
    "yoy": "yoy",
    "yoy %": "yoy",
    "year over year": "yoy",
    "year-over-year": "yoy",
}
# Derives that read a value *over time* — they need ``month`` in the grain.
_TIME_DERIVES = {"growth", "latest", "rolling", "index", "cumulative", "yoy"}
# Derives that only make sense for an additive total (you sum parts, not averages).
_SUM_ONLY_DERIVES = {"share", "cumulative"}


def _canon_derive(raw: Any) -> str:
    return _DERIVE_ALIASES.get(str(raw or "").strip().lower(), "")


def _measure(
    raw: Any,
    *,
    default_label: str,
    default_source: str,
    default_num: str | None = None,
    default_den: str | None = None,
) -> dict[str, Any]:
    """Normalise a measure dict from the form into ``{label, base, …, months,
    derive}``.

    ``base`` is the aggregation — ``sum``/``mean`` of one ``source`` column, or a
    weighted average ``wavg`` of ``num``/``den``. ``derive`` (optional) augments
    that base over the window: ``share`` (% of the total within the series),
    ``growth`` (recent-window vs prior-window % change), ``latest``, ``rolling``
    (mean over the window), ``index`` (rebased to 100), ``cumulative`` (running
    total), ``rank`` (within the series), ``yoy`` (vs 12 months prior). All
    deterministic. Old goldens stored the augmentation as ``how`` — it's mapped
    forward, and an old augmented ``how`` implied a ``sum`` base.

    ``default_num``/``default_den`` recompose a recomposed-average default: when
    the form supplies no measure at all and the fallback is a non-additive ratio
    column (e.g. ``avg_sale_price``), build it as a wavg of the additive legs
    rather than summing the ratio — which the additive guard rightly rejects — so
    a partial (or empty) spec still yields runnable code.
    """
    m = raw if isinstance(raw, dict) else {}
    label = str(m.get("label") or default_label)
    months = int(m.get("months") or 0) or None
    derive = _canon_derive(m.get("derive") or m.get("how") or "")
    num = m.get("num")
    den = m.get("den")
    if not num and not den and not m.get("source") and not derive and default_num and default_den:
        num, den = default_num, default_den
    source = str(m.get("source") or default_source)
    agg = str(m.get("agg") or "").strip().lower()
    common = {"label": label, "months": months, "derive": derive}
    # Weighted average: explicit agg=wavg, or a num/den pair with no plain agg.
    if agg == "wavg" or (num and den and agg not in ("sum", "mean")):
        return {**common, "base": "wavg", "num": str(num or source), "den": str(den or source)}
    return {**common, "base": ("mean" if agg == "mean" else "sum"), "source": source}


def dimension_cols(raw: Any, prof: MartProfile) -> list[str]:
    """The x-axis source column(s). A list is a *composite* axis (e.g.
    ``["bedroom_band", "property_type"]`` → one ``band · type`` axis)."""
    if isinstance(raw, list):
        cols = [str(c) for c in raw if c]
        return cols or [prof.default_grain[1]]
    return [str(raw or prof.default_grain[1])]


def _x_axis_lines(dim_cols: list[str]) -> tuple[str, list[str]]:
    """(x_col, code) — a single column, or a synthesized ``_x`` that joins several
    dimension columns into one nominal axis label (``concat(x1, '-', x2, …)``)."""
    if len(dim_cols) > 1:
        concat = " + '-' + ".join(f"base[{json.dumps(c)}].astype(str)" for c in dim_cols)
        return "_x", [f"base['_x'] = {concat}"]
    return dim_cols[0], []


def measure_source_cols(spec: dict[str, Any]) -> set[str]:
    """The source/num/den columns the spec's measures read from the extract."""
    cols: set[str] = set()
    pivot = spec.get("pivot_measures")
    raws = [spec.get("bar_measure"), spec.get("line_measure")]
    if isinstance(pivot, list):
        raws += list(pivot)
    for raw in raws:
        if not isinstance(raw, dict):
            continue
        for k in ("source", "num", "den"):
            if raw.get(k):
                cols.add(str(raw[k]))
    return {c for c in cols if c}


def needed_columns(spec: dict[str, Any]) -> set[str]:
    """Every source column the spec's grain + measures read from the extract."""
    cols: set[str] = set(spec.get("grain") or [])
    dim = spec.get("dimension")
    if isinstance(dim, list):
        cols.update(str(c) for c in dim if c)
    elif dim:
        cols.add(str(dim))
    if spec.get("group"):
        cols.add(str(spec["group"]))
    if spec.get("pivot_column"):
        cols.add(str(spec["pivot_column"]))
    cols |= measure_source_cols(spec)
    return {c for c in cols if c}


def _typed_grain(spec: dict[str, Any], prof: MartProfile) -> list[str]:
    return [str(c) for c in (spec.get("grain") or prof.default_grain) if c]


def _grain_with_chart_cols(spec: dict[str, Any], prof: MartProfile) -> list[str]:
    """Typed grain plus the dimension/group columns a bar-family snippet groups
    by — the single source for both the rewritten extract's grain and the
    codegen's window-dedup grain, so the two can never drift apart."""
    grain = _typed_grain(spec, prof)
    for col in (
        *dimension_cols(spec.get("dimension"), prof),
        *([str(spec["group"])] if spec.get("group") else []),
    ):
        if col and col not in grain:
            grain.append(col)
    return grain


def _trend_axis(spec: dict[str, Any]) -> str:
    """A trend's x column. Any grain column works, but an unset ``dimension``
    means the time axis — NOT ``dimension_cols``' first-categorical fallback,
    which would silently turn a time series into a line over bedroom bands."""
    raw = spec.get("dimension")
    cols = [str(c) for c in raw if c] if isinstance(raw, list) else ([str(raw)] if raw else [])
    return cols[0] if cols else "month"


def _trend_grain(spec: dict[str, Any], prof: MartProfile) -> list[str]:
    """Typed grain plus the trend's x axis and series column — the extract has to
    carry both, because the snippet aggregates to exactly (x, group)."""
    grain = _typed_grain(spec, prof)
    for col in (_trend_axis(spec), str(spec.get("group") or "")):
        if col and col not in grain:
            grain.append(col)
    return grain


def _pivot_grain(spec: dict[str, Any], prof: MartProfile) -> list[str]:
    """Typed grain plus the pivot's row columns and its pivoted column — the one
    place both the rewritten extract and the codegen read it from, so the extract
    can never be grouped coarser than the cross-tab it has to fill."""
    grain = _typed_grain(spec, prof)
    for col in (*dimension_cols(spec.get("dimension"), prof), str(spec.get("pivot_column") or "")):
        if col and col not in grain:
            grain.append(col)
    return grain


def extract_grain(
    spec: dict[str, Any], *, object_type: str, dataset: str = "nsw_sales"
) -> list[str]:
    """The grain the rewritten canonical extract must carry for ``spec``.

    Bar-family objects (compare/breakdown/table) append the dimension/group
    columns their snippet groups by, sharing ``_grain_with_chart_cols`` with the
    codegen's ``_bar_grain``. Trend/kpi keep the typed grain untouched apart
    from defensively appending ``group`` (if set) so ``trend_series``'s
    ``group_col`` is always present in the extract even for a spec authored
    before the frontend enforced group-is-a-grain-member invariant:
    ``trend_series``/``latest_value`` read the extract per month, so a finer
    grain would otherwise change their numbers."""
    prof = profile_for(dataset)
    if object_type == "pivot":
        return _pivot_grain(spec, prof)
    if object_type == "trend":
        # Its x axis is a real dimension now, and the snippet aggregates the
        # extract to exactly (x, group) — so both must be in the grain.
        return _trend_grain(spec, prof)
    if object_type == "kpi":
        # No axis: the typed grain, plus `group` defensively so a spec authored
        # before the group-is-a-grain-member invariant still resolves.
        grain = _typed_grain(spec, prof)
        group = spec.get("group")
        if group and str(group) not in grain:
            grain.append(str(group))
        return grain
    return _grain_with_chart_cols(spec, prof)


# ---------------------------------------------------------------------------
# Extract extension — regenerate a canonical extract at the object's grain,
# carrying the golden's suburb / property_type filters.
# ---------------------------------------------------------------------------


# Column names are interpolated into the extract SQL (SELECT/GROUP BY/ORDER BY,
# sum(...)) and into the generated snippet — only a plain lowercase identifier
# is ever a real mart column, so anything else (e.g. a parenthesised nested
# SELECT) is rejected with the same posture validate_where_override applies to
# the filter field.
_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")

# Bucket-level averages/medians/extremes and derived ratios — summing them
# across grain rows is silently wrong (a sum-of-averages), and the window dedup
# sums every measure source (a mean measure included), so every raw-column
# aggregate refuses them; ratios must be recomposed via a num/den weighted
# average.
_NON_ADDITIVE = re.compile(r"^(?:avg|median|min|max)_|^gross_yield_pct$")


def _ident(col: str) -> str:
    if not _IDENT.match(col):
        raise ValueError(f"invalid column identifier: {col!r}")
    return col


def _additive_source(col: str) -> str:
    if _NON_ADDITIVE.match(col):
        raise ValueError(
            f"column {col!r} is not additive and cannot be summed; "
            "recompose it as a num/den weighted average instead"
        )
    return col


# A WHERE-clause fragment should never itself contain a nested statement — an
# admin-authored `filter` string that could, e.g., correlate a subquery into an
# RLS-exempt table (app.users) would let a single-table extract read data it
# has no business seeing. Same posture as sql_guardrails._FORBIDDEN, plus a ban
# on nested SELECTs/CTEs, since a predicate fragment never legitimately needs one.
_FILTER_FORBIDDEN = re.compile(
    r"\b(select|union|insert|update|delete|drop|alter|truncate|create|grant|revoke|"
    r"copy|merge|call|into|with)\b",
    re.IGNORECASE,
)


def validate_where_override(where_override: str) -> str:
    """Reject anything beyond a simple predicate fragment (no statement
    separators, comments, or nested statements) so a builder `filter` field
    can't reach outside the object's own extract."""
    frag = where_override.strip()
    if ";" in frag or "--" in frag or "/*" in frag:
        raise ValueError("filter may not contain statement separators or comments")
    if _FILTER_FORBIDDEN.search(frag):
        raise ValueError("filter may not contain SQL keywords beyond a predicate")
    return frag


# The golden's original WHERE clause is preserved verbatim on every rewrite: the
# object is a *summary of the same governed rows the question already scoped*, so
# its extract must never drop or replace that filter. The builder's `filter` field
# only ANDs a further predicate on top — an object can narrow, never widen.
# Scanned at paren-depth 0 so a subquery/CTE's own WHERE (nested inside
# parentheses) is skipped in favour of the outer query's WHERE.
_WHERE_START_RE = re.compile(r"[()]|\bWHERE\b", re.IGNORECASE)
_WHERE_END_RE = re.compile(
    r"[()]|\bGROUP\s+BY\b|\bORDER\s+BY\b|\bHAVING\b|\bLIMIT\b|\bWINDOW\b|;",
    re.IGNORECASE,
)


def original_where(base_sql: str) -> str:
    """The base extract's outermost WHERE predicate, verbatim and
    whitespace-collapsed (or empty). The base SQL is the golden's own
    admin-authored extract, so it is carried as-is — the builder never
    re-derives, widens, or drops it; it only ANDs the optional ``filter`` field
    on top. Any WHERE nested inside parentheses (a subquery or CTE) is skipped
    so a nested predicate is never mistaken for the outer query's own filter."""
    sql = base_sql or ""
    depth = 0
    where_start = None
    for m in _WHERE_START_RE.finditer(sql):
        tok = m.group(0)
        if tok == "(":
            depth += 1
        elif tok == ")":
            depth = max(depth - 1, 0)
        elif depth == 0:
            where_start = m.end()
            break
    if where_start is None:
        return ""
    depth = 0
    where_end = len(sql)
    for m in _WHERE_END_RE.finditer(sql, where_start):
        tok = m.group(0)
        if tok == "(":
            depth += 1
        elif tok == ")":
            if depth == 0:
                where_end = m.start()
                break
            depth -= 1
        elif depth == 0:
            where_end = m.start()
            break
    return " ".join(sql[where_start:where_end].split())


def canonical_extract_sql(
    base_sql: str,
    *,
    grain: list[str],
    measure_source_cols: set[str],
    where_override: str = "",
    dataset: str = "nsw_sales",
    table: str | None = None,
) -> str:
    """A canonical extract at ``grain`` that SELECTs the profile's additive legs
    (+ a recomposed average convenience column, e.g. ``avg_sale_price`` for sales,
    ``avg_weekly_rent`` for rent).

    ``dataset`` selects the mart profile (table, additive legs, recomposed ratio).
    ``measure_source_cols`` are the spec's measure source columns — any not already
    covered by the grain or the profile's legs (e.g. ``n_sold`` on the yield mart)
    are summed too, so the extract carries every column the generated snippet reads.

    The golden's original WHERE is **always preserved verbatim** — the object is a
    summary of the same governed rows the question already scoped, so a rewrite
    never drops or replaces that filter. ``where_override`` (the builder's optional
    ``filter`` field) is ANDed *on top* as an additional predicate — an object can
    narrow the golden's rows further, never widen them."""
    prof = profile_for(dataset)
    tbl = table or prof.table
    grain_cols = [_ident(c) for c in grain if c] or list(prof.default_grain)
    # Always carry both additive legs so the ratio can be recomposed correctly.
    select = [*grain_cols]
    for c in prof.additive:
        select.append(f"sum({c}) AS {c}")
    select.append(
        f"round((sum({prof.value_col}) / NULLIF(sum({prof.count_col}), 0))::numeric) "
        f"AS {prof.ratio_col}"
    )
    covered = {*grain_cols, *prof.additive, prof.ratio_col}
    for c in sorted(measure_source_cols):
        if c and c not in covered:
            select.append(f"sum({_additive_source(_ident(c))}) AS {c}")
            covered.add(c)
    # Preserve the golden's original filter verbatim, then AND the builder's
    # optional additional predicate. A single part is emitted bare; two parts are
    # each parenthesised so their operator precedence can't cross-contaminate.
    add = validate_where_override(where_override) if where_override.strip() else ""
    parts = [p for p in (original_where(base_sql), add) if p]
    if not parts:
        where_sql = ""
    elif len(parts) == 1:
        where_sql = "\nWHERE " + parts[0]
    else:
        where_sql = "\nWHERE " + "\n  AND ".join(f"({p})" for p in parts)
    return (
        "SELECT\n  "
        + ",\n  ".join(select)
        + f"\nFROM {tbl}"
        + where_sql
        + "\nGROUP BY "
        + ", ".join(grain_cols)
        + "\nORDER BY "
        + ", ".join(grain_cols)
    )


# ---------------------------------------------------------------------------
# Code generation — deterministic run_analysis for the object.
# ---------------------------------------------------------------------------


# Derives a LINE CHART can carry. A trend keeps one value per point, so the
# derive has to be a transform of the series — not the window collapse
# `_measure_block` performs for the bar family. `latest`/`rank` collapse a series
# to a single number and `rolling` is the smoothing control, so none of the three
# is a line; they are rejected by name rather than silently ignored.
_SERIES_DERIVES = {"growth", "yoy", "index", "cumulative", "share"}

# Extra months a series derive needs BEFORE the window it draws, because each
# point is read against an earlier one. `index` rebases to the window's own
# first point and `share`/`cumulative` look sideways or forward, so they need
# nothing.
_SERIES_LOOKBACK = {"yoy": 12, "growth": 1}


def _series_derive_lines(
    measure: dict[str, Any], x: str, group: str | None, keys: list[str]
) -> list[str]:
    """Transform a trend's value column in place, per series, ordered by x.

    * ``growth``     — % change against the previous point
    * ``yoy``        — % change against 12 points back
    * ``index``      — rebased to 100 at the series' first point
    * ``cumulative`` — running total along the series
    * ``share``      — % of all series' total AT EACH x (a mix over time)
    """
    derive = measure.get("derive") or ""
    if not derive:
        return []
    label = measure["label"]
    # growth / yoy / index / cumulative read a value ALONG the axis, so the axis
    # has to be time. Over categories each series is one point and pct_change
    # yields nothing — an empty chart rather than an error. `share` is exempt: it
    # compares series to each other at each x, whatever x is.
    if derive != "share" and x != "month":
        raise ValueError(
            f"{derive!r} on {label!r} reads a change over TIME, but this chart's x axis "
            f"is {x!r}. Put month on the x axis, or use 'share'."
        )
    if derive not in _SERIES_DERIVES:
        raise ValueError(
            f"{derive!r} cannot be drawn as a line: it reduces {label!r} to a single "
            "value. Use it on a bar/table object, or pick growth / yoy / index / "
            "cumulative / share for a trend."
        )
    lab = json.dumps(label)
    lines = [
        f"agg = agg.sort_values({json.dumps(keys)}, kind='stable')",
        # One series per group; ungrouped is a single series, so a constant key
        # keeps the grouped and ungrouped paths identical.
        f"agg['_sk'] = agg[{json.dumps(group)}] if {bool(group)} else 0",
    ]
    if derive == "growth":
        lines.append(f"agg[{lab}] = agg.groupby('_sk')[{lab}].pct_change() * 100")
    elif derive == "yoy":
        lines.append(f"agg[{lab}] = agg.groupby('_sk')[{lab}].pct_change(12) * 100")
    elif derive == "index":
        lines.append(
            f"agg[{lab}] = agg[{lab}] * 100.0 / "
            f"agg.groupby('_sk')[{lab}].transform('first').where(lambda v: v != 0)"
        )
    elif derive == "cumulative":
        lines.append(f"agg[{lab}] = agg.groupby('_sk')[{lab}].cumsum()")
    else:  # share
        lines.append(
            f"agg[{lab}] = agg[{lab}] * 100.0 / "
            f"agg.groupby({json.dumps(x)})[{lab}].transform('sum').where(lambda v: v != 0)"
        )
    lines += [f"agg[{lab}] = agg[{lab}].round(2)", "agg = agg.drop(columns=['_sk'])"]
    # Rename the column to say what it now IS. A growth on avg_weekly_rent is a
    # percentage, so leaving the label as the money column makes the legend and
    # the axis claim dollars.
    derived_label = f"{label} {derive}"
    lines.append(f"agg = agg.rename(columns={{{lab}: {json.dumps(derived_label)}}})")
    measure["label"] = derived_label
    return lines


def _sort_lines(spec: dict[str, Any], var: str) -> list[str]:
    """Order the object's rows by the spec's ``sort`` list.

    ``sort`` is ORDERED — ``[{"col": "suburb"}, {"col": "n_sold", "dir": "desc"}]``
    sorts by suburb, then breaks ties by volume descending — so the curator
    controls priority by ordering the list, not by a hidden rule. Columns are
    matched against the frame AT RUNTIME because a pivot's metric columns are
    named after data values (``avg_weekly_rent · 2077``) and cannot be checked
    here; a name that isn't there is skipped rather than raising.

    A stable sort, so ties keep the order the aggregation produced instead of
    shuffling between otherwise identical runs (the graders diff row order).
    """
    entries = spec.get("sort")
    if not isinstance(entries, list):
        return []
    pairs = [
        (str(e.get("col") or ""), str(e.get("dir") or "asc") != "desc")
        for e in entries
        if isinstance(e, dict) and e.get("col")
    ]
    if not pairs:
        return []
    return [
        # repr, not json.dumps: the ascending flags are Python bools and JSON
        # would render them `false`/`true`, which the sandbox has no names for.
        f"_sort = [(c, a) for c, a in {pairs!r} if c in {var}.columns]",
        "if _sort:",
        f"    {var} = {var}.sort_values(",
        "        [c for c, _ in _sort], ascending=[a for _, a in _sort], kind='stable'",
        "    )",
    ]


def _dedup_cols(prof: MartProfile, *measures: dict[str, Any]) -> list[str]:
    """The union of the profile's additive legs and the measures' source columns
    — every column the window dedup must sum so a measure over a non-profile leg
    (e.g. ``n_sold`` on the yield mart) survives to the measure blocks."""
    cols = list(prof.additive)
    for m in measures:
        for k in ("source", "num", "den"):
            v = str(m.get(k) or "")
            if v and v not in cols:
                cols.append(v)
    return cols


def _window_setup(grain: list[str], months: int | None, additive: list[str]) -> list[str]:
    """Dedup df to ``grain`` (summing the additive/source cols present) and expose
    the latest-N-month windows as python sets the measure blocks filter on."""
    keys = json.dumps(grain)
    add = json.dumps(list(additive))
    lines = [
        "work = df.copy()",
        # Grain columns the extract doesn't carry are dropped rather than raising:
        # you can only dedup by what is present, and the window dedup only SUMS
        # additive legs, so a coarser dedup yields the same numbers once the
        # measure blocks aggregate to the chart's own keys. A column the chart
        # genuinely needs still fails loudly, at the chart groupby.
        f"_grain = [c for c in {keys} if c in work.columns]",
        f"_add = {add}",
        "_agg = {c: (c, 'sum') for c in _add if c in work.columns}",
        "base = work.groupby(_grain, as_index=False).agg(**_agg) if (_agg and _grain) else work",
    ]
    if "month" in grain:
        lines += [
            "_months = sorted(m for m in base['month'].dropna().unique()) "
            "if 'month' in base.columns else []",
            f"_win_default = set(_months[-{int(months or 12)}:])",
        ]
    else:
        lines += ["_win_default = None"]
    return lines


def _measure_block(
    m: dict[str, Any],
    keys: list[str],
    var: str,
    has_month: bool,
    within: list[str] | None = None,
    default_months: int = 12,
) -> list[str]:
    """Emit pandas that builds one measure at ``keys`` grain into DataFrame ``var``
    with a single value column named ``m['label']``.

    The measure is a BASE aggregation over the window (``sum``/``mean`` of one
    column, or a weighted-average ``num``/``den``) plus an optional DERIVE that
    augments it. The base's monthly additive components are built once into
    ``<var>_c`` (``_num``/``_den`` per key per month, and ``_v`` = the monthly
    value); the derive then reduces them to one value per key:

    * ``""``          — the value aggregated over the window.
    * ``share``       — % of the window value within ``within`` (each series sums
                        to 100% across the x-axis); empty ``within`` ⇒ grand total.
    * ``rank``        — dense rank of the window value within ``within`` (1 = top).
    * ``growth``      — the window value over the recent ``w`` months vs the prior
                        ``w`` months, as a % change (period over period).
    * ``latest``      — the most recent month's value.
    * ``rolling``     — the mean of the monthly values over the window.
    * ``index``       — the latest value ÷ the window's first value × 100.
    * ``cumulative``  — the running total over the window (a sum's window total).
    * ``yoy``         — the latest month vs 12 months prior, as a % change.

    ``share``/``cumulative`` need an additive (``sum``) base; the time derives need
    ``month`` in the grain — both are rejected honestly rather than silently
    collapsing to a static aggregate while the label still claims otherwise.
    """
    keys_lit = json.dumps(keys)
    label = json.dumps(m["label"])
    base = m["base"]
    derive = m.get("derive") or ""
    additive = base == "sum"
    within_lit = json.dumps([c for c in (within or []) if c])
    w = int(m.get("months") or 0) or default_months

    if derive in _TIME_DERIVES and not has_month:
        raise ValueError(
            f"{derive} measure {m['label']!r} needs 'month' in the grain — it is a "
            "change over time, not a static aggregate"
        )
    if derive in _SUM_ONLY_DERIVES and not additive:
        raise ValueError(
            f"{derive} measure {m['label']!r} needs a sum aggregation — % of total / "
            "cumulative are only meaningful for an additive total"
        )

    # Monthly additive components at (keys[, month]). For a sum the value IS the
    # numerator; for mean/wavg it is num/den (count for mean, denominator leg for
    # wavg) — so any period aggregates correctly by summing the legs first.
    if base == "wavg":
        num, den = json.dumps(_additive_source(m["num"])), json.dumps(_additive_source(m["den"]))
        comp = f"_num=({num}, 'sum'), _den=({den}, 'sum')"
    elif base == "mean":
        sc = json.dumps(_additive_source(m["source"]))
        comp = f"_num=({sc}, 'sum'), _den=({sc}, 'count')"
    else:
        comp = f"_num=({json.dumps(_additive_source(m['source']))}, 'sum')"
    mkeys_lit = json.dumps([*keys, *(["month"] if has_month else [])])
    lines = [f"{var}_c = base.groupby({mkeys_lit}, as_index=False).agg({comp})"]
    lines.append(
        f"{var}_c['_v'] = {var}_c['_num']"
        if additive
        else f"{var}_c['_v'] = ({var}_c['_num'] / {var}_c['_den'].where({var}_c['_den'] != 0))"
    )
    win_slice = f"{var}_c[{var}_c['month'].isin(set(_months[-{w}:]))]" if has_month else f"{var}_c"

    def agg_val(df_expr: str, out: str, col: str) -> list[str]:
        """Sum the components of ``df_expr`` to one value per key in ``out[col]``."""
        if additive:
            return [
                f"{out} = {df_expr}.groupby({keys_lit}, as_index=False).agg(_num=('_num', 'sum'))",
                f"{out}[{col}] = {out}['_num']",
            ]
        return [
            f"{out} = {df_expr}.groupby({keys_lit}, as_index=False)"
            f".agg(_num=('_num', 'sum'), _den=('_den', 'sum'))",
            f"{out}[{col}] = ({out}['_num'] / {out}['_den'].where({out}['_den'] != 0))",
        ]

    tail = f"{var} = {var}[{keys_lit} + [{label}]]"

    if derive == "share":
        lines += [
            f"{var} = {win_slice}.groupby({keys_lit}, as_index=False).agg(_num=('_num', 'sum'))",
            f"_wl = {within_lit}",
            f"_den = ({var}.groupby(_wl)['_num'].transform('sum') if _wl "
            f"else pd.Series({var}['_num'].sum(), index={var}.index))",
            f"{var}[{label}] = "
            f"({var}['_num'] * 100.0 / _den.where(_den != 0)).round(2).fillna(0.0)",
            tail,
        ]
    elif derive == "cumulative":
        lines += [
            f"{var} = {win_slice}.groupby({keys_lit}, as_index=False).agg(_num=('_num', 'sum'))",
            f"{var}[{label}] = {var}['_num']",
            tail,
        ]
    elif derive == "rank":
        lines += agg_val(win_slice, var, "'_val'")
        lines += [
            f"_wl = {within_lit}",
            f"{var}[{label}] = ({var}.groupby(_wl)['_val'].rank(ascending=False, method='dense') "
            f"if _wl else {var}['_val'].rank(ascending=False, method='dense')).astype(int)",
            tail,
        ]
    elif derive == "growth":
        lines += [f"_rec = set(_months[-{w}:])", f"_pri = set(_months[-{2 * w}:-{w}])"]
        lines += agg_val(f"{var}_c[{var}_c['month'].isin(_rec)]", f"{var}_r", "'_r'")
        lines += agg_val(f"{var}_c[{var}_c['month'].isin(_pri)]", f"{var}_p", "'_p'")
        lines += [
            f"{var} = {var}_r[{keys_lit} + ['_r']].merge({var}_p[{keys_lit} + ['_p']], "
            f"on={keys_lit}, how='left')",
            f"{var}[{label}] = (({var}['_r'] - {var}['_p']) * 100.0 / "
            f"{var}['_p'].where({var}['_p'] != 0)).round(1).fillna(0.0)",
            tail,
        ]
    elif derive == "latest":
        lines += ["_lm = _months[-1]"]
        lines += agg_val(f"{var}_c[{var}_c['month'] == _lm]", var, label)
        lines += [f"{var}[{label}] = {var}[{label}].round()", tail]
    elif derive == "rolling":
        lines += [
            f"{var} = {win_slice}.groupby({keys_lit}, as_index=False)['_v'].mean()",
            f"{var} = {var}.rename(columns={{'_v': {label}}})",
            f"{var}[{label}] = {var}[{label}].round()",
            tail,
        ]
    elif derive == "index":
        lines += [
            f"_wm = _months[-{w}:]",
            f"_first = {var}_c[{var}_c['month'] == _wm[0]]"
            f"[{keys_lit} + ['_v']].rename(columns={{'_v': '_f'}})",
            f"_last = {var}_c[{var}_c['month'] == _wm[-1]]"
            f"[{keys_lit} + ['_v']].rename(columns={{'_v': '_l'}})",
            f"{var} = _last.merge(_first, on={keys_lit}, how='left')",
            f"{var}[{label}] = "
            f"({var}['_l'] / {var}['_f'].where({var}['_f'] != 0) * 100.0).round(1).fillna(0.0)",
            tail,
        ]
    elif derive == "yoy":
        lines += [
            "_lm = _months[-1]",
            "_pm = _months[-13:][0]",
            f"_last = {var}_c[{var}_c['month'] == _lm]"
            f"[{keys_lit} + ['_v']].rename(columns={{'_v': '_l'}})",
            f"_prior = {var}_c[{var}_c['month'] == _pm]"
            f"[{keys_lit} + ['_v']].rename(columns={{'_v': '_p'}})",
            f"{var} = _last.merge(_prior, on={keys_lit}, how='left')",
            f"{var}[{label}] = (({var}['_l'] - {var}['_p']) * 100.0 / "
            f"{var}['_p'].where({var}['_p'] != 0)).round(1).fillna(0.0)",
            tail,
        ]
    else:  # "" — the plain window aggregate (unchanged from the pre-derive builder)
        lines += agg_val(win_slice, var, label)
        if not additive:
            lines.append(f"{var}[{label}] = {var}[{label}].round()")
        lines.append(tail)
    return lines


def _bar_grain(spec: dict[str, Any], prof: MartProfile) -> tuple[list[str], list[str], str | None]:
    """Grain + dimension column(s) + group for a bar-family chart, ensuring the
    chart's own columns survive the window dedup."""
    dim_cols = dimension_cols(spec.get("dimension"), prof)
    group = spec.get("group") or None
    return _grain_with_chart_cols(spec, prof), dim_cols, (str(group) if group else None)


def _combo_code(spec: dict[str, Any], prof: MartProfile) -> str:
    grain, dim_cols, group = _bar_grain(spec, prof)
    has_month = "month" in grain
    bar = _measure(spec.get("bar_measure"), default_label="volume", default_source=prof.count_col)
    line = _measure(
        spec.get("line_measure"),
        default_label=prof.ratio_col,
        default_source=prof.ratio_col,
        default_num=prof.value_col,
        default_den=prof.count_col,
    )
    lines = _window_setup(grain, int(spec.get("months") or 12), _dedup_cols(prof, bar, line))
    x_col, x_lines = _x_axis_lines(dim_cols)
    lines += x_lines
    chart_keys = [x_col] + ([group] if group else [])
    # A share bar is a share *within the series* — each series sums to 100% across
    # the x-axis (the "mix" reading). No series ⇒ share of the grand total.
    within = [group] if group else []
    lines += _measure_block(
        bar,
        chart_keys,
        "bar_df",
        has_month,
        within=within,
        default_months=int(spec.get("months") or 12),
    )
    lines += _measure_block(
        line,
        chart_keys,
        "line_df",
        has_month,
        within=within,
        default_months=int(spec.get("months") or 12),
    )
    keys_lit = json.dumps(chart_keys)
    lines += [
        f"agg = bar_df.merge(line_df, on={keys_lit}, how='left')",
        f"agg = agg.dropna(subset=[{json.dumps(line['label'])}])",
    ]
    series = f", series_col={json.dumps(group)}" if group else ""
    title = json.dumps(spec.get("title") or "Sale price vs volume by band")
    summary = json.dumps(
        spec.get("summary") or "Bars compare volume; the line tracks price across the dimension."
    )
    lines += [
        "chart = skills.dual_axis_chart(",
        "    agg,",
        f"    x_col={json.dumps(x_col)},",
        f"    left_value_col={json.dumps(bar['label'])},",
        f"    right_value_col={json.dumps(line['label'])},",
        "    x_type='nominal',",
        f"    left_title={json.dumps(bar['label'])},",
        f"    right_title={json.dumps(line['label'])}{series},",
        f"    left_unit={json.dumps(unit_for_measure(bar))},",
        f"    right_unit={json.dumps(unit_for_measure(line))},",
        f"    title={title},",
        ")",
        f"result = skills.build_report(summary={summary}, main_chart=chart)",
    ]
    return "\n".join(lines)


def _breakdown_code(spec: dict[str, Any], prof: MartProfile) -> str:
    grain, dim_cols, group = _bar_grain(spec, prof)
    has_month = "month" in grain
    bar = _measure(spec.get("bar_measure"), default_label="volume", default_source=prof.count_col)
    lines = _window_setup(grain, int(spec.get("months") or 12), _dedup_cols(prof, bar))
    x_col, x_lines = _x_axis_lines(dim_cols)
    lines += x_lines
    chart_keys = [x_col] + ([group] if group else [])
    within = [group] if group else []
    lines += _measure_block(
        bar,
        chart_keys,
        "agg",
        has_month,
        within=within,
        default_months=int(spec.get("months") or 12),
    )
    lines += _sort_lines(spec, "agg")
    series = f", series_col={json.dumps(group)}" if group else ""
    x_label = " · ".join(dim_cols)
    title = json.dumps(spec.get("title") or f"{bar['label']} by {x_label}")
    summary = json.dumps(spec.get("summary") or f"{bar['label']} compared across {x_label}.")
    lines += [
        "chart = skills.comparison_chart(",
        "    agg,",
        f"    category_col={json.dumps(x_col)},",
        f"    value_col={json.dumps(bar['label'])}{series},",
        f"    value_unit={json.dumps(unit_for_measure(bar))},",
        f"    title={title},",
        ")",
        f"result = skills.build_report(summary={summary}, main_chart=chart)",
    ]
    return "\n".join(lines)


def _trend_code(spec: dict[str, Any], prof: MartProfile) -> str:
    """A line chart of one measure over an x axis, optionally split into series.

    The x axis is the spec's ``dimension`` (``month`` for the usual time series,
    but any grain column works — e.g. a line across bedroom_band).

    The extract is AGGREGATED TO THE CHART'S OWN GRAIN first. ``trend_series``
    emits one point per row it is handed, so feeding it a raw extract that is
    finer than (x, group) drew several points on the same x and divided a ratio
    per row instead of recomposing it from summed legs. Summing the additive
    legs here and dividing after is the same ratio-of-sums rule the bar family
    already followed.
    """
    x = _trend_axis(spec)
    group = spec.get("group") or None
    group = str(group) if group else None
    line = _measure(
        spec.get("line_measure") or spec.get("bar_measure"),
        default_label=prof.ratio_col,
        default_source=prof.ratio_col,
        default_num=prof.value_col,
        default_den=prof.count_col,
    )
    grain = _trend_grain(spec, prof)
    has_month = "month" in grain
    months = int(spec.get("months") or 12)
    keys = [x] + ([group] if group and group != x else [])
    label = line["label"]

    # Monthly additive components, exactly as _measure_block builds them: a sum
    # is its own numerator; a mean is sum/count; a wavg is num/den.
    if line["base"] == "wavg":
        num, den = (
            json.dumps(_additive_source(line["num"])),
            json.dumps(_additive_source(line["den"])),
        )
        comp = f"_num=({num}, 'sum'), _den=({den}, 'sum')"
        ratio = True
    elif line["base"] == "mean":
        sc = json.dumps(_additive_source(line["source"]))
        comp = f"_num=({sc}, 'sum'), _den=({sc}, 'count')"
        ratio = True
    else:
        comp = f"_num=({json.dumps(_additive_source(line['source']))}, 'sum')"
        ratio = False

    # A time derive reads each point against an EARLIER one, so the window has
    # to reach back past the first point the chart draws: a YoY over the latest
    # 12 months had no prior year inside that window to compare with, and drew
    # an empty chart. The extra months are consumed by the derive, not plotted.
    lookback = _SERIES_LOOKBACK.get(line.get("derive") or "", 0)
    lines = _window_setup(grain, months + lookback, _dedup_cols(prof, line))
    # Only a time axis gets the latest-N-month window; over categories the window
    # would silently drop rows the axis is meant to show.
    src = "base[base['month'].isin(_win_default)]" if has_month and x == "month" else "base"
    lines.append(f"agg = {src}.groupby({json.dumps(keys)}, as_index=False).agg({comp})")
    lines.append(
        f"agg[{json.dumps(label)}] = (agg['_num'] / agg['_den'].where(agg['_den'] != 0))"
        if ratio
        else f"agg[{json.dumps(label)}] = agg['_num']"
    )
    lines += _series_derive_lines(line, x, group, keys)
    # _series_derive_lines renames the column to include the derive, so re-read
    # the label before it is used to address the column downstream.
    label = line["label"]

    date_axis = x == "month"
    group_arg = f", group_col={json.dumps(group)}" if group and group != x else ""
    date_arg = "" if date_axis else ", date_axis=False"
    x_type = "" if date_axis else ", x_type='ordinal'"
    # The smoothing overlay, both halves of it, are the curator's call: `window`
    # sizes the rolling average (0 = none) and `show_actual` decides whether the
    # faint unsmoothed line is drawn under it. Both were hardcoded in here.
    window = spec.get("rolling_window")
    window = 6 if window is None else int(window)
    show_actual = spec.get("show_actual")
    show_actual = True if show_actual is None else bool(show_actual)
    if date_axis:
        date_arg += f", window={window}"
        if not show_actual:
            date_arg += ", show_actual=False"
    over = "over time" if date_axis else f"by {x}"
    title = json.dumps(spec.get("title") or f"{label} {over}")
    summary = json.dumps(spec.get("summary") or f"{label} {over}.")
    lines += [
        f"series = skills.trend_series(agg, month_col={json.dumps(x)}, "
        f"value_col={json.dumps(label)}{group_arg}{date_arg})",
        # The derive is already folded into the measure's unit — a growth line
        # over avg_weekly_rent is a percentage, not the dollars it came from.
        f"chart = skills.trend_chart(series, title={title}{x_type}, "
        f"y_label={json.dumps(label)}, y_unit={json.dumps(unit_for_measure(line))})",
        f"result = skills.build_report(summary={summary}, main_chart=chart)",
    ]
    return "\n".join(lines)


def _kpi_code(spec: dict[str, Any], prof: MartProfile) -> str:
    m = _measure(
        spec.get("line_measure") or spec.get("bar_measure"),
        default_label=prof.ratio_col,
        default_source=prof.ratio_col,
        default_num=prof.value_col,
        default_den=prof.count_col,
    )
    if m["base"] == "wavg":
        val = f"value_col={json.dumps(m['num'])}, den_col={json.dumps(m['den'])}"
    else:
        val = f"value_col={json.dumps(m['source'])}"
    label = json.dumps(m["label"])
    summary = json.dumps(spec.get("summary") or f"Latest {m['label']}.")
    return "\n".join(
        [
            f"latest = skills.latest_value(df, month_col='month', {val})",
            "headline = {'label': " + label + ", 'value': latest.get('value'), "
            f"'format': {json.dumps(unit_for_measure(m))}, "
            "'basis': 'latest 6-mo avg · ' + str(latest.get('month'))}",
            f"result = skills.build_report(summary={summary}, headlines=[headline])",
        ]
    )


def _table_code(spec: dict[str, Any], prof: MartProfile) -> str:
    """A ranked/plain data table at the chart grain — the s20 ``table`` object.

    Aggregates the bar measure (and, when present, the line measure as a second
    value column) to ``dimension`` (+ optional ``group``), then emits the
    DataTable wire shape via ``skills.data_table`` for ``build_report(table=...)``.
    A list ``dimension`` becomes one synthesized composite axis column, exactly
    as in compare/breakdown.
    """
    grain, dim_cols, group = _bar_grain(spec, prof)
    has_month = "month" in grain
    bar = _measure(spec.get("bar_measure"), default_label="volume", default_source=prof.count_col)
    measures = [bar]
    line_raw = spec.get("line_measure")
    if isinstance(line_raw, dict) and (line_raw.get("source") or line_raw.get("num")):
        measures.append(
            _measure(line_raw, default_label=prof.ratio_col, default_source=prof.ratio_col)
        )

    lines = _window_setup(grain, int(spec.get("months") or 12), _dedup_cols(prof, *measures))
    x_col, x_lines = _x_axis_lines(dim_cols)
    lines += x_lines
    chart_keys = [x_col] + ([group] if group else [])
    keys_lit = json.dumps(chart_keys)
    within = [group] if group else []
    lines += _measure_block(
        measures[0],
        chart_keys,
        "agg",
        has_month,
        within=within,
        default_months=int(spec.get("months") or 12),
    )
    if len(measures) > 1:
        lines += _measure_block(
            measures[1],
            chart_keys,
            "m2",
            has_month,
            within=within,
            default_months=int(spec.get("months") or 12),
        )
        lines += [f"agg = agg.merge(m2, on={keys_lit}, how='left')"]

    variant = str(spec.get("variant") or "ranked")
    if variant not in ("plain", "comparison", "ranked"):
        variant = "ranked"
    bar_label = measures[0]["label"]
    if variant == "ranked":
        lines += [f"agg = agg.sort_values({json.dumps(bar_label)}, ascending=False)"]
    # Applied last, so an explicit sort overrides the ranked variant's default.
    lines += _sort_lines(spec, "agg")

    x_label = " · ".join(dim_cols)
    columns = [{"key": x_col, "label": x_label}]
    if group:
        columns.append({"key": group, "label": group})
    # Conditional formatting: colour the measure cells by sign (green up, red
    # down). Only the numbers — a row label has no sign to read.
    tone = {"tone": "delta"} if spec.get("color_by_sign") else {}
    # Each measure column states its own unit, so a $ column and a % column in
    # the same table are both read correctly (the cell renderer never guesses).
    columns += [
        {
            "key": m["label"],
            "label": m["label"],
            "align": "right",
            "format": unit_for_measure(m),
            **tone,
        }
        for m in measures
    ]
    title = json.dumps(spec.get("title") or f"{bar_label} by {x_label}")
    summary = json.dumps(spec.get("summary") or f"{bar_label} tabulated by {x_label}.")
    bar_key = json.dumps(bar_label if variant == "ranked" else None)
    lines += [
        "table = skills.data_table(",
        "    agg,",
        f"    columns={json.dumps(columns)},",
        f"    title={title},",
        f"    variant={json.dumps(variant)},",
        f"    bar_key={bar_key},",
        ")",
        f"result = skills.build_report(summary={summary}, table=table)",
    ]
    return "\n".join(lines)


def pivot_measures(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """The pivot's metrics, normalised. Falls back to the bar/line measures so a
    spec authored as another table type still pivots if it is switched over."""
    raw = spec.get("pivot_measures")
    if isinstance(raw, list) and raw:
        return [_measure(m, default_label="value", default_source="") for m in raw if m]
    return []


def _dedup_pivot_labels(measures: list[dict[str, Any]]) -> None:
    """Disambiguate pivot measures that share a label, mirroring the trend path's
    ``_series_derive_lines`` (which appends the derive to a re-derived series'
    label). Two ``pivot_measures`` entries with the same label but different
    ``derive`` would otherwise collide on ``agg.merge``'s shared column name —
    pandas silently suffixes it ``_x``/``_y``, and every downstream lookup by
    label (``_vals``/``_units``/``piv.pivot_table``) breaks."""
    counts: dict[str, int] = {}
    for m in measures:
        counts[m["label"]] = counts.get(m["label"], 0) + 1
    used = set(counts.keys())
    for m in measures:
        label = m["label"]
        if counts[label] <= 1:
            continue
        derive = m.get("derive") or ""
        candidate = f"{label} {derive}" if derive else label
        n = 1
        final = candidate
        while final in used and final != label:
            n += 1
            final = f"{candidate} {n}"
        used.add(final)
        m["label"] = final


def _pivot_code(spec: dict[str, Any], prof: MartProfile) -> str:
    """A cross-tab: rows x one pivoted dimension, each cell a set of metrics.

    ``dimension`` gives the row columns and ``pivot_column`` the dimension whose
    VALUES become column groups, so ``rows = bedroom_band, property_type`` and
    ``columns = postcode`` reads as one row per dwelling with a block of metrics
    per postcode. That is the shape a long table can't give you: with postcode as
    just another row column, comparing two postcodes means scanning up and down
    instead of across.

    Every metric goes through the same ``_measure_block`` the charts use, so a
    ratio (avg_weekly_rent) is recomposed as a weighted average at the pivot's
    grain rather than averaged-of-averages, and the window/derive rules are
    identical. The pivoted column labels depend on the DATA, so the column list
    is assembled at runtime rather than baked into the snippet.
    """
    rows = dimension_cols(spec.get("dimension"), prof)
    col = str(spec.get("pivot_column") or "")
    if not col:
        raise ValueError("pivot needs a pivot_column")
    if col in rows:
        raise ValueError(f"pivot_column {col!r} is also a row column — pick a different one")
    measures = pivot_measures(spec)
    if not measures:
        raise ValueError("pivot needs at least one metric")
    _dedup_pivot_labels(measures)

    months = int(spec.get("months") or 12)
    grain = _pivot_grain(spec, prof)
    has_month = "month" in grain
    keys = [*rows, col]

    lines = _window_setup(grain, months, _dedup_cols(prof, *measures))
    for i, m in enumerate(measures):
        var = f"_m{i}"
        lines += _measure_block(m, keys, var, has_month, within=[], default_months=months)
        lines.append(
            f"agg = {var}"
            if i == 0
            else f"agg = agg.merge({var}, on={json.dumps(keys)}, how='outer')"
        )

    labels = [m["label"] for m in measures]
    lines += [
        f"_rows = {json.dumps(rows)}",
        f"_col = {json.dumps(col)}",
        f"_vals = {json.dumps(labels)}",
        # Unit per metric. The cross-tab's column NAMES are data ("· 2077"), so
        # the format is looked up per generated column below rather than being
        # readable off the name — the same metric twice with different derives
        # (rent, rent growth) is exactly the case a name can't resolve.
        f"_units = {json.dumps({m['label']: unit_for_measure(m) for m in measures})}",
        # Sorted so the column order is stable run to run (the grader diffs it).
        "_col_vals = sorted(str(v) for v in agg[_col].dropna().unique())",
        "agg[_col] = agg[_col].astype(str)",
        "piv = agg.pivot_table(index=_rows, columns=_col, values=_vals, aggfunc='first')",
        # pivot_table gives (metric, column-value) pairs; flatten to one column per
        # pair. METRIC-MAJOR: all of avg_weekly_rent's postcodes sit together, then
        # all of bonds'. Grouping the other way (every metric of 2076, then every
        # metric of 2077) puts the two numbers you actually want to compare at
        # opposite ends of the row.
        "piv.columns = [str(mv) + ' · ' + str(cv) for mv, cv in piv.columns]",
        "_order = [mv + ' · ' + cv for mv in _vals for cv in _col_vals "
        "if mv + ' · ' + cv in piv.columns]",
        "piv = piv[_order].reset_index()",
        "_fmt = {mv + ' · ' + cv: _units.get(mv, 'number') for mv in _vals for cv in _col_vals}",
        # Names of columns whose SIGN carries the meaning (the Δs), so the
        # renderer can colour them green up / red down.
        "_delta_names = set()",
    ]

    # A difference column per metric, across the pivoted values. This is the
    # whole reason to pivot two postcodes side by side, so the table can state
    # the gap instead of leaving it to be read off two numbers.
    compare = str(spec.get("pivot_compare") or "")
    if compare in ("diff", "pct_diff"):
        expr = (
            "(_b - _a)" if compare == "diff" else "((_b - _a) * 100.0 / _a.where(_a != 0)).round(2)"
        )
        suffix = " · Δ" if compare == "diff" else " · Δ%"
        # A raw gap is measured in the metric's own unit ($200 more rent); a
        # relative gap is a percentage whatever the metric was.
        gap_unit = "_units.get(mv, 'number')" if compare == "diff" else "'percent'"
        lines += [
            "_diff_cols = []",
            # Only meaningful across exactly two values; with more, the "gap" has
            # no single definition, so the columns are left off rather than
            # guessing which pair the reader meant.
            "if len(_col_vals) == 2:",
            "    for mv in _vals:",
            "        _ca, _cb = mv + ' · ' + _col_vals[0], mv + ' · ' + _col_vals[1]",
            "        if _ca in piv.columns and _cb in piv.columns:",
            "            _a, _b = piv[_ca], piv[_cb]",
            f"            _name = mv + {json.dumps(suffix)}",
            f"            piv[_name] = {expr}",
            "            _diff_cols.append((_cb, _name))",
            "            _delta_names.add(_name)",
            f"            _fmt[_name] = {gap_unit}",
            # Insert each Δ directly after its metric's last column, so it reads
            # as part of that metric's block rather than in a trailing clump.
            "    for _after, _name in _diff_cols:",
            "        _order.insert(_order.index(_after) + 1, _name)",
            "    piv = piv[_rows + _order]",
        ]

    # Conditional formatting: colour a numeric cell by its sign (green up, red
    # down). A difference column is ALWAYS coloured — a signed gap is the one
    # number whose direction is the point — and `color_by_sign` extends it to
    # every metric column.
    sign_all = bool(spec.get("color_by_sign"))
    lines += _sort_lines(spec, "piv")
    lines += [
        "columns = [{'key': r, 'label': r} for r in _rows] + [",
        "    dict({'key': c, 'label': c, 'align': 'right', 'format': _fmt.get(c, 'number')},",
        f"         **({{'tone': 'delta'}} if ({str(sign_all)} or c in _delta_names) else {{}}))",
        "    for c in _order",
        "]",
    ]
    row_label = " · ".join(rows)
    title = json.dumps(spec.get("title") or f"{row_label} by {col}")
    summary = json.dumps(
        spec.get("summary") or f"{', '.join(labels)} for each {row_label}, pivoted by {col}."
    )
    lines += [
        "table = skills.data_table(",
        "    piv,",
        "    columns=columns,",
        f"    title={title},",
        "    variant='plain',",
        ")",
        f"result = skills.build_report(summary={summary}, table=table)",
    ]
    return "\n".join(lines)


_BUILDERS = {
    "compare": _combo_code,
    "breakdown": _breakdown_code,
    "trend": _trend_code,
    "kpi": _kpi_code,
    "table": _table_code,
    "pivot": _pivot_code,
}


def build_object_code(*, object_type: str, spec: dict[str, Any], dataset: str = "nsw_sales") -> str:
    """Deterministic run_analysis snippet for a named presentation object.

    ``dataset`` selects the mart profile (additive legs + default grain/measures),
    so a rent object aggregates ``n_rented``/``total_weekly_rent`` while a sales
    object aggregates ``n_sold``/``total_sale_value`` (s22 P2). ``df`` (the shared
    extract), ``pd`` and ``skills`` are already in scope in the sandbox — the
    snippet never imports. Ends in ``result = skills.build_report(...)``.

    Raises ``ValueError`` when the spec names a column that isn't a plain
    identifier, or aggregates a non-additive column (``avg_*``/``median_*``/
    ``gross_yield_pct`` — those must go through the num/den wavg path).
    """
    prof = profile_for(dataset)
    spec = spec if isinstance(spec, dict) else {}
    for c in sorted(needed_columns(spec)):
        _ident(c)
    builder = _BUILDERS.get(object_type, _combo_code)
    return builder(spec, prof)
