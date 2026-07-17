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
from typing import Any

from .schema import SALES_MART

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

# Additive raw columns that may be summed across a re-aggregation window.
_ADDITIVE = ("n_sold", "total_sale_value")


def _measure(raw: Any, *, default_label: str, default_source: str) -> dict[str, Any]:
    """Normalise a measure dict from the form.

    Shapes accepted:
      * ``{"label","source","agg","months"}``          — sum/mean of one column
      * ``{"label","num","den","months"}``              — weighted avg num/den
    """
    m = raw if isinstance(raw, dict) else {}
    num = m.get("num")
    den = m.get("den")
    label = str(m.get("label") or default_label)
    months = int(m.get("months") or 0) or None
    if num and den:
        return {"kind": "wavg", "label": label, "num": str(num), "den": str(den), "months": months}
    return {
        "kind": "agg",
        "label": label,
        "source": str(m.get("source") or default_source),
        "agg": str(m.get("agg") or "sum"),
        "months": months,
    }


def needed_columns(spec: dict[str, Any]) -> set[str]:
    """Every source column the spec's grain + measures read from the extract."""
    cols: set[str] = set(spec.get("grain") or [])
    for key in ("dimension", "group"):
        if spec.get(key):
            cols.add(str(spec[key]))
    for raw in (spec.get("bar_measure"), spec.get("line_measure")):
        if not isinstance(raw, dict):
            continue
        for k in ("source", "num", "den"):
            if raw.get(k):
                cols.add(str(raw[k]))
    return {c for c in cols if c}


# ---------------------------------------------------------------------------
# Extract extension — regenerate a canonical extract at the object's grain,
# carrying the golden's suburb / property_type filters.
# ---------------------------------------------------------------------------


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


def _carry_filters(base_sql: str) -> list[str]:
    """Best-effort WHERE predicates lifted from the golden's current extract:
    the suburb IN/= list and a property_type = '…' filter (verbatim values)."""
    preds: list[str] = []
    sql = base_sql or ""
    m = re.search(r"\bsuburb\s+IN\s*\(([^)]*)\)", sql, re.IGNORECASE)
    if m and m.group(1).strip():
        preds.append(f"suburb IN ({m.group(1).strip()})")
    else:
        m = re.search(r"\bsuburb\s*=\s*('[^']*')", sql, re.IGNORECASE)
        if m:
            preds.append(f"suburb = {m.group(1)}")
    m = re.search(r"\bproperty_type\s*=\s*('[^']*')", sql, re.IGNORECASE)
    if m:
        preds.append(f"property_type = {m.group(1)}")
    return preds


def canonical_extract_sql(
    base_sql: str,
    *,
    grain: list[str],
    measure_source_cols: set[str],
    where_override: str = "",
    table: str = SALES_MART,
) -> str:
    """A canonical extract at ``grain`` that SELECTs the additive measure sources
    (+ a recomposed ``avg_sale_price`` convenience column).

    ``where_override`` is the exact WHERE predicate to scope the object (from the
    builder's filter field, e.g. ``property_type = 'house' AND suburb IN (...)``);
    when empty the golden's suburb / property_type filters are best-effort carried
    from ``base_sql`` instead. The filter is surfaced in the form so it's editable
    lineage, not a hidden guess."""
    grain_cols = [c for c in grain if c] or ["suburb", "area_band", "month"]
    # Always carry both additive sources so avg_sale_price can be recomposed.
    additive = list(_ADDITIVE)
    select = [*grain_cols]
    for c in additive:
        select.append(f"sum({c}) AS {c}")
    if {"n_sold", "total_sale_value"} <= set(additive):
        select.append(
            "round((sum(total_sale_value) / NULLIF(sum(n_sold), 0))::numeric) AS avg_sale_price"
        )
    where = (
        [validate_where_override(where_override)]
        if where_override.strip()
        else _carry_filters(base_sql)
    )
    where_sql = ("\nWHERE " + "\n  AND ".join(where)) if where else ""
    return (
        "SELECT\n  "
        + ",\n  ".join(select)
        + f"\nFROM {table}"
        + where_sql
        + "\nGROUP BY "
        + ", ".join(grain_cols)
        + "\nORDER BY "
        + ", ".join(grain_cols)
    )


# ---------------------------------------------------------------------------
# Code generation — deterministic run_analysis for the object.
# ---------------------------------------------------------------------------


def _window_setup(grain: list[str], months: int | None) -> list[str]:
    """Dedup df to ``grain`` (summing additive cols) and expose the latest-N-month
    windows as python sets the measure blocks filter on."""
    keys = json.dumps(grain)
    lines = [
        "work = df.copy()",
        f"_grain = {keys}",
        "_agg = {c: (c, 'sum') for c in ('n_sold', 'total_sale_value') if c in work.columns}",
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


def _measure_block(m: dict[str, Any], keys: list[str], var: str, has_month: bool) -> list[str]:
    """Emit pandas that builds one measure at ``keys`` grain into DataFrame ``var``
    with a single value column named ``m['label']``."""
    keys_lit = json.dumps(keys)
    win = m.get("months")
    src = (
        f"base[base['month'].isin(set(_months[-{int(win)}:]))]"
        if (has_month and win)
        else ("base[base['month'].isin(_win_default)]" if has_month else "base")
    )
    label = m["label"]
    if m["kind"] == "wavg":
        return [
            f"{var} = {src}.groupby({keys_lit}, as_index=False).agg("
            f"_num=('{m['num']}', 'sum'), _den=('{m['den']}', 'sum'))",
            f"{var}['{label}'] = ({var}['_num'] / {var}['_den'].where({var}['_den'] != 0)).round()",
            f"{var} = {var}[{keys_lit} + ['{label}']]",
        ]
    agg = "mean" if m["agg"] == "mean" else "sum"
    round_ = ".round()" if agg == "mean" else ""
    return [
        f"{var} = {src}.groupby({keys_lit}, as_index=False)['{m['source']}'].{agg}(){round_}",
        f"{var} = {var}.rename(columns={{'{m['source']}': '{label}'}})",
    ]


def _combo_code(spec: dict[str, Any]) -> str:
    grain = spec.get("grain") or ["month", "suburb", "area_band"]
    dimension = str(spec.get("dimension") or "area_band")
    group = spec.get("group") or None
    has_month = "month" in grain
    bar = _measure(spec.get("bar_measure"), default_label="sales_volume", default_source="n_sold")
    line = _measure(
        spec.get("line_measure"),
        default_label="avg_sale_price",
        default_source="avg_sale_price",
    )
    chart_keys = [dimension] + ([str(group)] if group else [])
    lines = _window_setup(grain, int(spec.get("months") or 12))
    lines += _measure_block(bar, chart_keys, "bar_df", has_month)
    lines += _measure_block(line, chart_keys, "line_df", has_month)
    keys_lit = json.dumps(chart_keys)
    lines += [
        f"agg = bar_df.merge(line_df, on={keys_lit}, how='left')",
        f"agg = agg.dropna(subset=['{line['label']}'])",
    ]
    series = f", series_col={json.dumps(str(group))}" if group else ""
    title = json.dumps(spec.get("title") or "Sale price vs volume by band")
    summary = json.dumps(
        spec.get("summary") or "Bars compare volume; the line tracks price across the dimension."
    )
    lines += [
        "chart = skills.dual_axis_chart(",
        "    agg,",
        f"    x_col={json.dumps(dimension)},",
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


def _breakdown_code(spec: dict[str, Any]) -> str:
    grain = spec.get("grain") or ["suburb", "area_band"]
    dimension = str(spec.get("dimension") or "area_band")
    group = spec.get("group") or None
    has_month = "month" in grain
    bar = _measure(spec.get("bar_measure"), default_label="sales_volume", default_source="n_sold")
    chart_keys = [dimension] + ([str(group)] if group else [])
    lines = _window_setup(grain, int(spec.get("months") or 12))
    lines += _measure_block(bar, chart_keys, "agg", has_month)
    series = f", series_col={json.dumps(str(group))}" if group else ""
    title = json.dumps(spec.get("title") or f"{bar['label']} by {dimension}")
    summary = json.dumps(spec.get("summary") or f"{bar['label']} compared across {dimension}.")
    lines += [
        "chart = skills.comparison_chart(",
        "    agg,",
        f"    category_col={json.dumps(dimension)},",
        f"    value_col={json.dumps(bar['label'])}{series},",
        f"    title={title},",
        ")",
        f"result = skills.build_report(summary={summary}, main_chart=chart)",
    ]
    return "\n".join(lines)


def _trend_code(spec: dict[str, Any]) -> str:
    group = spec.get("group") or None
    line = _measure(
        spec.get("line_measure") or spec.get("bar_measure"),
        default_label="avg_sale_price",
        default_source="avg_sale_price",
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


def _kpi_code(spec: dict[str, Any]) -> str:
    m = _measure(
        spec.get("line_measure") or spec.get("bar_measure"),
        default_label="avg_sale_price",
        default_source="avg_sale_price",
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


def _table_code(spec: dict[str, Any]) -> str:
    """A ranked/plain data table at the chart grain — the s20 ``table`` object.

    Aggregates the bar measure (and, when present, the line measure as a second
    value column) to ``dimension`` (+ optional ``group``), then emits the
    DataTable wire shape via ``skills.data_table`` for ``build_report(table=...)``.
    """
    grain = spec.get("grain") or ["suburb", "area_band"]
    dimension = str(spec.get("dimension") or "area_band")
    group = spec.get("group") or None
    has_month = "month" in grain
    bar = _measure(spec.get("bar_measure"), default_label="sales_volume", default_source="n_sold")
    measures = [bar]
    line_raw = spec.get("line_measure")
    if isinstance(line_raw, dict) and (line_raw.get("source") or line_raw.get("num")):
        measures.append(
            _measure(line_raw, default_label="avg_sale_price", default_source="avg_sale_price")
        )
    chart_keys = [dimension] + ([str(group)] if group else [])
    keys_lit = json.dumps(chart_keys)

    lines = _window_setup(grain, int(spec.get("months") or 12))
    lines += _measure_block(measures[0], chart_keys, "agg", has_month)
    if len(measures) > 1:
        lines += _measure_block(measures[1], chart_keys, "m2", has_month)
        lines += [f"agg = agg.merge(m2, on={keys_lit}, how='left')"]

    variant = str(spec.get("variant") or "ranked")
    if variant not in ("plain", "comparison", "ranked"):
        variant = "ranked"
    bar_label = measures[0]["label"]
    if variant == "ranked":
        lines += [f"agg = agg.sort_values({json.dumps(bar_label)}, ascending=False)"]

    columns = [{"key": k, "label": k} for k in chart_keys] + [
        {"key": m["label"], "label": m["label"], "align": "right"} for m in measures
    ]
    title = json.dumps(spec.get("title") or f"{bar_label} by {dimension}")
    summary = json.dumps(spec.get("summary") or f"{bar_label} tabulated by {dimension}.")
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


def build_object_code(*, object_type: str, spec: dict[str, Any]) -> str:
    """Deterministic run_analysis snippet for a named presentation object.

    ``df`` (the shared extract), ``pd`` and ``skills`` are already in scope in the
    sandbox — the snippet never imports. Ends in ``result = skills.build_report(...)``.
    """
    builder = _BUILDERS.get(object_type, _combo_code)
    return builder(spec if isinstance(spec, dict) else {})
