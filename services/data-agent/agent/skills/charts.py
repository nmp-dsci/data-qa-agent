"""Visualisation skills — chart specs that match the app's house style.

These reuse ``agent.chart`` so a sandbox-built chart is validated by the same
allowlist and styled by the same deterministic overlay as ``make_chart`` today —
the frontend renders it identically. The skill returns a full Vega-Lite spec
with ``data.values`` already spliced in (the sandbox holds real, governed rows,
so there is no id-indirection to resolve later).

Replaces knowledge pages: trend-charts.md, comparison-charts.md, profile-sections.md.
"""

from __future__ import annotations

from typing import Any, cast

import pandas as pd

from ..chart import trend_overlay_encoding, validate_chart_spec
from . import skill

_MAX_POINTS = 2000


def _q(field: str, *, title: str | None = None, unit: str | None = None) -> dict[str, Any]:
    """A quantitative encoding channel carrying what its numbers ARE.

    ``unit`` (currency / number / percent — see agent/units.py) rides on the
    channel so the page object lifted from this spec can hand it to the
    renderer. Without it the axis and tooltip fall back to guessing from the
    field name, which for a ``value`` column means dollars.
    """
    enc: dict[str, Any] = {"field": field, "type": "quantitative", "title": title}
    if unit:
        enc["unit"] = unit
    return enc


@skill
def trend_chart(
    series_df: pd.DataFrame,
    *,
    title: str | None = None,
    x_type: str = "temporal",
    y_label: str | None = None,
    y_unit: str | None = None,
) -> dict[str, Any]:
    """A time-series trend chart: faint actual under a bold N-month rolling line.

    ``series_df`` is the long-form frame from ``trend_series`` (columns
    ``month, value, series, layer``). The actual+rolling overlay styling is
    enforced deterministically (opacity + stroke weight per layer, colour per
    series), the app's convention — not left to the model.

    ``x_type`` must match the axis the series were built on: ``temporal`` for a
    month axis, ``ordinal`` for a line over categories (``trend_series`` with
    ``date_axis=False``). Declaring a category axis temporal renders it as
    unparseable dates.

    ``y_label`` names what the y values ARE and ``y_unit`` says how to format
    them (currency / number / percent). The frame's value column is always
    called ``value``, so without the pair every trend was labelled in dollars,
    a bond count included.
    """
    values = cast(list[dict[str, Any]], series_df.to_dict("records"))
    base_enc = {
        "x": {"field": "month", "type": x_type, "title": None},
        "y": _q("value", title=y_label, unit=y_unit),
    }
    spec: dict[str, Any] = {
        "mark": "line",
        "encoding": trend_overlay_encoding(base_enc, values),
    }
    if title:
        spec["title"] = title
    validated = validate_chart_spec(spec)
    return {**validated, "data": {"values": values[:_MAX_POINTS]}}


@skill
def comparison_chart(
    df: pd.DataFrame,
    *,
    category_col: str,
    value_col: str,
    title: str | None = None,
    series_col: str | None = None,
    value_unit: str | None = None,
) -> dict[str, Any]:
    """A (grouped) bar chart comparing a metric across entities.

    Use inside an insight card to compare entities side-by-side — e.g. 5-year
    growth per suburb. ``series_col`` adds a grouped/colour dimension.
    ``value_unit`` says what the bars measure (currency / number / percent).
    """
    encoding: dict[str, Any] = {
        "x": {"field": category_col, "type": "nominal", "sort": "-y"},
        "y": _q(value_col, unit=value_unit),
    }
    if series_col:
        encoding["color"] = {"field": series_col, "type": "nominal", "title": None}
        encoding["xOffset"] = {"field": series_col, "type": "nominal"}
    spec: dict[str, Any] = {"mark": "bar", "encoding": encoding}
    if title:
        spec["title"] = title
    validated = validate_chart_spec(spec)
    values = df.to_dict("records")
    return {**validated, "data": {"values": values[:_MAX_POINTS]}}


@skill
def dual_axis_chart(
    df: pd.DataFrame,
    *,
    x_col: str,
    left_value_col: str,
    right_value_col: str,
    title: str | None = None,
    left_title: str | None = None,
    right_title: str | None = None,
    x_type: str = "temporal",
    series_col: str | None = None,
    left_unit: str | None = None,
    right_unit: str | None = None,
) -> dict[str, Any]:
    """Bars plus a secondary-axis line for two metrics with different scales.

    Use when the question compares two measures on the same x-axis but one scale
    would flatten the other, such as sales volume vs price or rent vs sale price.
    The only allowed independent scale is y, enforced by ``validate_chart_spec``.

    ``series_col`` adds a grouped/colour dimension (e.g. suburb): the bars cluster
    by series (``xOffset``) and one line is drawn per series, so two entities can
    be compared bars-and-line on the same x-axis (e.g. volume + price per suburb).
    The two axes carry their own units — the whole point of a dual axis is that
    the measures differ, so one of them being dollars says nothing about the other.
    """
    values = df.to_dict("records")
    bar_enc: dict[str, Any] = {
        "x": {"field": x_col, "type": x_type, "title": None},
        "y": _q(left_value_col, title=left_title or left_value_col, unit=left_unit),
    }
    line_enc: dict[str, Any] = {
        "x": {"field": x_col, "type": x_type, "title": None},
        "y": _q(right_value_col, title=right_title or right_value_col, unit=right_unit),
    }
    if series_col:
        bar_enc["color"] = {"field": series_col, "type": "nominal", "title": None}
        bar_enc["xOffset"] = {"field": series_col, "type": "nominal"}
        line_enc["color"] = {"field": series_col, "type": "nominal", "title": None}
    spec: dict[str, Any] = {
        "layer": [
            {"mark": "bar", "encoding": bar_enc},
            {"mark": {"type": "line", "point": True}, "encoding": line_enc},
        ],
        "resolve": {"scale": {"y": "independent"}},
    }
    if title:
        spec["title"] = title
    validated = validate_chart_spec(spec)
    return {**validated, "data": {"values": values[:_MAX_POINTS]}}


@skill
def distribution_chart(
    df: pd.DataFrame,
    *,
    value_col: str,
    title: str | None = None,
    category_col: str | None = None,
    value_unit: str | None = None,
) -> dict[str, Any]:
    """Histogram for spread, outliers, and distribution questions.

    Use when the user asks about the spread of a numeric measure rather than a
    trend or ranked comparison. ``category_col`` colours distributions by group;
    ``value_unit`` says what the binned measure is (the y bars are always counts).
    """
    values = df.to_dict("records")
    x_enc = _q(value_col, unit=value_unit)
    x_enc["bin"] = True
    encoding: dict[str, Any] = {
        "x": x_enc,
        # The bars are a count of records whatever the binned measure is.
        "y": {"aggregate": "count", "type": "quantitative", "title": "Count", "unit": "number"},
    }
    if category_col:
        encoding["color"] = {"field": category_col, "type": "nominal", "title": None}
    spec: dict[str, Any] = {"mark": "bar", "encoding": encoding}
    if title:
        spec["title"] = title
    validated = validate_chart_spec(spec)
    return {**validated, "data": {"values": values[:_MAX_POINTS]}}


@skill
def profile_chart(
    df: pd.DataFrame,
    *,
    category_col: str,
    segment_col: str,
    value_col: str,
    title: str | None = None,
    normalize: bool = True,
    value_unit: str | None = None,
) -> dict[str, Any]:
    """Stacked composition bars comparing entities' *mix* across a segmenter.

    The profile-section chart: one stacked bar per entity (``category_col``, e.g.
    suburb), split by ``segment_col`` (e.g. bedroom band or lot-size band), so you
    can see how each entity's composition differs — the "smaller lots explain the
    price gap" story. With ``normalize`` (default), each entity's segments are
    converted to percentage shares summing to 100 in pandas, so the bars are
    directly comparable regardless of volume; set ``normalize=False`` to stack the
    raw ``value_col`` counts instead.

    Pick the segmenter by domain: lot-size/zoning for houses, bedroom band for
    units (see profile-sections.md) — never a sqm profile for apartments.
    """
    work = df.copy()
    if normalize:
        totals = work.groupby(category_col)[value_col].transform("sum")
        shares = (work[value_col] / totals.where(totals != 0)) * 100.0
        work[value_col] = shares.round(2)
    # Normalising converts the measure to a share of the entity's own total, so
    # the stacked bars are percentages regardless of what was summed.
    y_enc = _q(
        value_col,
        title="% share" if normalize else value_col,
        unit="percent" if normalize else value_unit,
    )
    y_enc["stack"] = "zero"
    encoding: dict[str, Any] = {
        "x": {"field": category_col, "type": "nominal"},
        "y": y_enc,
        "color": {"field": segment_col, "type": "nominal", "title": None},
    }
    spec: dict[str, Any] = {"mark": "bar", "encoding": encoding}
    if title:
        spec["title"] = title
    validated = validate_chart_spec(spec)
    values = work.to_dict("records")
    return {**validated, "data": {"values": values[:_MAX_POINTS]}}
