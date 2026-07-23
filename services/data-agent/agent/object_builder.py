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
    carry_cols: tuple[str, ...]

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
        carry_cols=("suburb", "property_type"),
    ),
    "nsw_rent": MartProfile(
        table=RENT_MART,
        count_col="n_rented",
        value_col="total_weekly_rent",
        ratio_col="avg_weekly_rent",
        default_grain=("postcode", "bedroom_band", "month"),
        carry_cols=("postcode", "property_type", "bedroom_band"),
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
        carry_cols=("postcode", "property_type"),
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


# The "how" modifier augments a base metric into a derived one, deterministically
# — so a count like ``n_sold`` becomes "% of sold by X" (share) or "growth over
# the window" without needing a stored derived column or the LLM.
_SHARE_HOWS = {"share", "pct", "percent", "% share", "share_of_x", "pct_of_x"}
_GROWTH_HOWS = {"growth", "growth %", "growth_pct", "delta", "delta %"}
_LATEST_HOWS = {"latest", "latest value", "current"}


def _measure(raw: Any, *, default_label: str, default_source: str) -> dict[str, Any]:
    """Normalise a measure dict from the form.

    Shapes accepted:
      * ``{"label","source","agg","months"}``          — sum/mean of one column
      * ``{"label","num","den","months"}``              — weighted avg num/den
      * ``{"label","source","how":"share"|"growth"|"latest","months"}``
        — a base metric *augmented*: ``share`` = % of the source within the
        chart's series (each series sums to 100% across the x-axis, the "mix"
        reading); ``growth`` = first-vs-last % change over the window; ``latest``
        = the value at the most recent month. All computed deterministically.
    """
    m = raw if isinstance(raw, dict) else {}
    num = m.get("num")
    den = m.get("den")
    label = str(m.get("label") or default_label)
    months = int(m.get("months") or 0) or None
    how = str(m.get("how") or "").strip().lower()
    source = str(m.get("source") or default_source)
    if num and den and how not in _SHARE_HOWS | _GROWTH_HOWS | _LATEST_HOWS:
        return {"kind": "wavg", "label": label, "num": str(num), "den": str(den), "months": months}
    if how in _SHARE_HOWS:
        return {"kind": "share", "label": label, "source": source, "months": months}
    if how in _GROWTH_HOWS:
        return {"kind": "growth", "label": label, "source": source, "months": months}
    if how in _LATEST_HOWS:
        return {"kind": "latest", "label": label, "source": source, "months": months}
    return {
        "kind": "agg",
        "label": label,
        "source": source,
        "agg": str(m.get("agg") or "sum"),
        "months": months,
    }


def dimension_cols(raw: Any, prof: MartProfile) -> list[str]:
    """The x-axis source column(s). A list is a *composite* axis (e.g.
    ``["bedroom_band", "property_type"]`` → one ``band · type`` axis)."""
    if isinstance(raw, list):
        cols = [str(c) for c in raw if c]
        return cols or [prof.default_grain[1]]
    return [str(raw or prof.default_grain[1])]


def _x_axis_lines(dim_cols: list[str]) -> tuple[str, list[str]]:
    """(x_col, code) — a single column, or a synthesized ``_x`` that joins several
    dimension columns into one nominal axis label."""
    if len(dim_cols) > 1:
        concat = " + ' · ' + ".join(f"base[{json.dumps(c)}].astype(str)" for c in dim_cols)
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
    codegen's ``_bar_grain``. Trend/kpi keep the typed grain untouched:
    ``trend_series``/``latest_value`` read the extract per month, so a finer
    grain would change their numbers."""
    prof = profile_for(dataset)
    if object_type in ("trend", "kpi"):
        return _typed_grain(spec, prof)
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


def _carry_filters(base_sql: str, carry_cols: tuple[str, ...]) -> list[str]:
    """Best-effort WHERE predicates lifted verbatim from the golden's current
    extract — an ``IN (...)`` list or an ``= '…'`` equality for each carried
    column (suburb/property_type for sales; postcode/property_type/bedroom_band
    for rent). ``carry_cols`` are fixed profile literals, never user input."""
    preds: list[str] = []
    sql = base_sql or ""
    for col in carry_cols:
        m = re.search(rf"\b{col}\s+IN\s*\(([^)]*)\)", sql, re.IGNORECASE)
        if m and m.group(1).strip():
            preds.append(f"{col} IN ({m.group(1).strip()})")
            continue
        m = re.search(rf"\b{col}\s*=\s*('[^']*')", sql, re.IGNORECASE)
        if m:
            preds.append(f"{col} = {m.group(1)}")
    return preds


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

    ``dataset`` selects the mart profile (table, additive legs, recomposed ratio,
    carried filters). ``measure_source_cols`` are the spec's measure source
    columns — any not already covered by the grain or the profile's legs (e.g.
    ``n_sold`` on the yield mart) are summed too, so the extract carries every
    column the generated snippet reads. ``where_override`` is the exact WHERE
    predicate to scope the object (from the builder's filter field, e.g.
    ``property_type = 'house' AND suburb IN (...)``); when empty the golden's
    carried filters are best-effort lifted from ``base_sql`` instead. The filter
    is surfaced in the form so it's editable lineage, not a hidden guess."""
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
    where = (
        [validate_where_override(where_override)]
        if where_override.strip()
        else _carry_filters(base_sql, prof.carry_cols)
    )
    where_sql = ("\nWHERE " + "\n  AND ".join(where)) if where else ""
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
) -> list[str]:
    """Emit pandas that builds one measure at ``keys`` grain into DataFrame ``var``
    with a single value column named ``m['label']``.

    ``within`` scopes a ``share`` measure's denominator (the series/group column,
    so each series sums to 100% across the x-axis); empty ⇒ share of the grand
    total.
    """
    keys_lit = json.dumps(keys)
    win = m.get("months")
    src = (
        f"base[base['month'].isin(set(_months[-{int(win)}:]))]"
        if (has_month and win)
        else ("base[base['month'].isin(_win_default)]" if has_month else "base")
    )
    label = json.dumps(m["label"])
    kind = m["kind"]
    if kind == "wavg":
        num = json.dumps(_additive_source(m["num"]))
        den = json.dumps(_additive_source(m["den"]))
        return [
            f"{var} = {src}.groupby({keys_lit}, as_index=False).agg("
            f"_num=({num}, 'sum'), _den=({den}, 'sum'))",
            f"{var}[{label}] = ({var}['_num'] / {var}['_den'].where({var}['_den'] != 0)).round()",
            f"{var} = {var}[{keys_lit} + [{label}]]",
        ]
    if kind == "share":
        # % of the source within the series (`within`); each series then sums to
        # 100% across the x-axis. Always a Series denominator so the empty-`within`
        # (grand-total) case divides cleanly too.
        src_col = json.dumps(_additive_source(m["source"]))
        within_lit = json.dumps([c for c in (within or []) if c])
        return [
            f"{var} = {src}.groupby({keys_lit}, as_index=False)[{src_col}].sum()",
            f"_wl = {within_lit}",
            f"_den = ({var}.groupby(_wl)[{src_col}].transform('sum') if _wl "
            f"else pd.Series({var}[{src_col}].sum(), index={var}.index))",
            f"{var}[{label}] = "
            f"({var}[{src_col}] * 100.0 / _den.where(_den != 0)).round(2).fillna(0.0)",
            f"{var} = {var}[{keys_lit} + [{label}]]",
        ]
    if kind in ("growth", "latest") and has_month:
        # Per chart key over the window's months: first-vs-last % change, or the
        # most recent value — computed on per-month totals of the source, so a
        # grain wider than the chart keys never leaks one sub-slice row into the
        # boundary months. Needs the month grain to order on.
        src_col = json.dumps(_additive_source(m["source"]))
        month_keys = keys if "month" in keys else [*keys, "month"]
        ordered = (
            f"{src}.groupby({json.dumps(month_keys)}, as_index=False)"
            f"[{src_col}].sum().sort_values('month')"
        )
        if kind == "latest":
            return [
                f"{var} = {ordered}.groupby({keys_lit}, as_index=False)[{src_col}].last()",
                f"{var} = {var}.rename(columns={{{src_col}: {label}}})",
            ]
        return [
            f"{var} = {ordered}.groupby({keys_lit}, as_index=False).agg("
            f"_first=({src_col}, 'first'), _last=({src_col}, 'last'))",
            f"{var}[{label}] = "
            f"(({var}['_last'] - {var}['_first']) * 100.0 / "
            f"{var}['_first'].where({var}['_first'] != 0))"
            f".round(1).fillna(0.0)",
            f"{var} = {var}[{keys_lit} + [{label}]]",
        ]
    # Plain aggregate (also the fallback when growth/latest lack a month grain).
    agg = "mean" if m.get("agg") == "mean" else "sum"
    round_ = ".round()" if agg == "mean" else ""
    src_col = json.dumps(_additive_source(m["source"]))
    return [
        f"{var} = {src}.groupby({keys_lit}, as_index=False)[{src_col}].{agg}(){round_}",
        f"{var} = {var}.rename(columns={{{src_col}: {label}}})",
    ]


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
    )
    lines = _window_setup(grain, int(spec.get("months") or 12), _dedup_cols(prof, bar, line))
    x_col, x_lines = _x_axis_lines(dim_cols)
    lines += x_lines
    chart_keys = [x_col] + ([group] if group else [])
    # A share bar is a share *within the series* — each series sums to 100% across
    # the x-axis (the "mix" reading). No series ⇒ share of the grand total.
    within = [group] if group else []
    lines += _measure_block(bar, chart_keys, "bar_df", has_month, within=within)
    lines += _measure_block(line, chart_keys, "line_df", has_month, within=within)
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
    lines += _measure_block(bar, chart_keys, "agg", has_month, within=within)
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
    )
    group_arg = f", group_col={json.dumps(str(group))}" if group else ""
    if line["kind"] == "wavg":
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
    )
    if m["kind"] == "wavg":
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
    lines += _measure_block(measures[0], chart_keys, "agg", has_month, within=within)
    if len(measures) > 1:
        lines += _measure_block(measures[1], chart_keys, "m2", has_month, within=within)
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
