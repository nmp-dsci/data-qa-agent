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


@skill
def trend_chart(series_df: pd.DataFrame, *, title: str | None = None) -> dict[str, Any]:
    """A time-series trend chart: faint actual under a bold N-month rolling line.

    ``series_df`` is the long-form frame from ``trend_series`` (columns
    ``month, value, series, layer``). The actual+rolling overlay styling is
    enforced deterministically (opacity + stroke weight per layer, colour per
    series), the app's convention — not left to the model.
    """
    values = cast(list[dict[str, Any]], series_df.to_dict("records"))
    base_enc = {
        "x": {"field": "month", "type": "temporal", "title": None},
        "y": {"field": "value", "type": "quantitative"},
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
) -> dict[str, Any]:
    """A (grouped) bar chart comparing a metric across entities.

    Use inside an insight card to compare entities side-by-side — e.g. 5-year
    growth per suburb. ``series_col`` adds a grouped/colour dimension.
    """
    encoding: dict[str, Any] = {
        "x": {"field": category_col, "type": "nominal", "sort": "-y"},
        "y": {"field": value_col, "type": "quantitative"},
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
def profile_chart(
    df: pd.DataFrame,
    *,
    category_col: str,
    segment_col: str,
    value_col: str,
    title: str | None = None,
    normalize: bool = True,
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
    y_enc: dict[str, Any] = {
        "field": value_col,
        "type": "quantitative",
        "stack": "zero",
        "title": "% share" if normalize else value_col,
    }
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
