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
    for raw in (spec.get("bar_measure"), spec.get("line_measure")):
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
    if object_type in ("trend", "kpi"):
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
        f"_grain = {keys}",
        f"_add = {add}",
        "_agg = {c: (c, 'sum') for c in _add if c in work.columns}",
        "base = work.groupby(_grain, as_index=False).agg(**_agg) if _agg else work",
    ]
    if "month" in grain:
        lines += [
            "_months = sorted(m for m in base['month'].dropna().unique())",
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
    series = f", series_col={json.dumps(group)}" if group else ""
    x_label = " · ".join(dim_cols)
    title = json.dumps(spec.get("title") or f"{bar['label']} by {x_label}")
    summary = json.dumps(spec.get("summary") or f"{bar['label']} compared across {x_label}.")
    lines += [
        "chart = skills.comparison_chart(",
        "    agg,",
        f"    category_col={json.dumps(x_col)},",
        f"    value_col={json.dumps(bar['label'])}{series},",
        f"    title={title},",
        ")",
        f"result = skills.build_report(summary={summary}, main_chart=chart)",
    ]
    return "\n".join(lines)


def _trend_code(spec: dict[str, Any], prof: MartProfile) -> str:
    group = spec.get("group") or None
    line = _measure(
        spec.get("line_measure") or spec.get("bar_measure"),
        default_label=prof.ratio_col,
        default_source=prof.ratio_col,
        default_num=prof.value_col,
        default_den=prof.count_col,
    )
    group_arg = f", group_col={json.dumps(str(group))}" if group else ""
    if line["base"] == "wavg":
        val = f"value_col={json.dumps(line['num'])}, den_col={json.dumps(line['den'])}"
    else:
        val = f"value_col={json.dumps(line['source'])}"
    title = json.dumps(spec.get("title") or f"{line['label']} over time")
    summary = json.dumps(spec.get("summary") or f"{line['label']} trend by month.")
    return "\n".join(
        [
            f"series = skills.trend_series(df, month_col='month', {val}{group_arg})",
            f"chart = skills.trend_chart(series, title={title})",
            f"result = skills.build_report(summary={summary}, main_chart=chart)",
        ]
    )


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

    x_label = " · ".join(dim_cols)
    columns = [{"key": x_col, "label": x_label}]
    if group:
        columns.append({"key": group, "label": group})
    columns += [{"key": m["label"], "label": m["label"], "align": "right"} for m in measures]
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


_BUILDERS = {
    "compare": _combo_code,
    "breakdown": _breakdown_code,
    "trend": _trend_code,
    "kpi": _kpi_code,
    "table": _table_code,
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
