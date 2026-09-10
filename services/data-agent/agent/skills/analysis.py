"""Data-analysis skills — growth, latest, rolling series, yield.

These wrap the proven, deterministic maths in ``agent.analytics`` (same code the
old ``compute_trend`` tool used) behind a DataFrame-first surface the sandbox
model calls. Reusing ``analytics`` means the restructure inherits the exact,
already-tested numbers — a 6-month rolling base for growth and "latest", every
month kept (the rolling average, not a row filter, absorbs thin-month noise).

Each skill accepts a pandas DataFrame (the governed extract) plus the column
names to read, and optionally a ``group_col`` to compute per entity. With no
group column a single value is returned; with one, a ``{group: value}`` dict.

Replaces knowledge pages: rolling-averages.md, growth-rates.md, latest-month.md,
yield.md.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from .. import analytics
from . import skill

_Series = list[dict[str, Any]]


def _grouped(
    df: pd.DataFrame,
    *,
    month_col: str,
    value_col: str,
    den_col: str | None,
    count_col: str | None,
    group_col: str | None,
) -> dict[str, _Series]:
    """Build ``{group: month-sorted series}`` via analytics.build_series.

    NaN → None so the deterministic maths (which treats None as "no data")
    behaves identically whether rows came from SQL or a pandas frame.
    """
    columns = list(df.columns)
    clean = df.astype(object).where(pd.notnull(df), None)
    rows = clean.values.tolist()
    return analytics.build_series(
        columns,
        rows,
        month_col=month_col,
        value_col=value_col,
        den_col=den_col,
        count_col=count_col,
        group_col=group_col,
    )


def _maybe_single(out: dict[str, Any], group_col: str | None) -> Any:
    """Return the bare value for an ungrouped call, else the per-group dict."""
    if group_col:
        return out
    return out.get("_all")


@skill
def trend_series(
    df: pd.DataFrame,
    *,
    month_col: str,
    value_col: str,
    den_col: str | None = None,
    group_col: str | None = None,
    window: int = 6,
    date_axis: bool = True,
    show_actual: bool = True,
) -> pd.DataFrame:
    """Long-form trend rows for charting: actual + N-month rolling per series.

    Returns a DataFrame with columns ``month, value, series, layer`` — the exact
    shape ``trend_chart`` expects and the same actual+rolling overlay the app
    renders today. Keeps every month; the rolling average absorbs noise.

    ``value = value_col / den_col`` when ``den_col`` is given (e.g.
    total_sale_value / n_sold → average price), else ``value_col`` directly.

    NOTE ON GRAIN: one point is emitted per ROW of ``df`` per group. Rows are not
    summed here, so ``df`` must already be at the (x, group) grain — pass an
    aggregated frame, never a raw extract that is finer than the chart. The
    object builder guarantees this; a finer frame would plot several points on
    the same x and compute a per-row ratio instead of a ratio of sums.

    ``date_axis=False`` plots a non-time x (e.g. bedroom_band): the x value is
    kept verbatim rather than being turned into a ``YYYY-MM-01`` date, and the
    rolling overlay is dropped — a rolling average over categories is not a
    meaningful quantity.

    The smoothing overlay is two independent choices. ``window`` sizes the
    rolling average (``0`` emits none, so only the actual line is drawn) and
    ``show_actual`` decides whether the faint unsmoothed line is drawn under it.
    Turning both off would leave nothing to plot, so ``show_actual`` is honoured
    only while a rolling layer exists.
    """
    keep_actual = show_actual or window <= 0
    grouped = _grouped(
        df,
        month_col=month_col,
        value_col=value_col,
        den_col=den_col,
        count_col=None,
        group_col=group_col,
    )
    rows: list[dict[str, Any]] = []
    for group, series in grouped.items():
        label = group if group != "_all" else value_col
        if not date_axis:
            rows.extend(
                {
                    "month": str(point["month"]),
                    "value": None if point["value"] is None else round(point["value"], 2),
                    "series": label,
                    "layer": "actual",
                }
                for point in series
                if point.get("value") is not None
            )
            continue
        if window <= 0:
            rows.extend(
                {
                    "month": f"{point['month']}-01",
                    "value": round(point["value"], 2),
                    "series": label,
                    "layer": "actual",
                }
                for point in series
                if point.get("value") is not None
            )
            continue
        for row in analytics.chart_series(series, rolling_window=window):
            if row["layer"] == "actual" and not keep_actual:
                continue
            rows.append(
                {
                    "month": f"{row['month']}-01",
                    "value": row["value"],
                    "series": label,
                    "layer": row["layer"],
                }
            )
    return pd.DataFrame(rows, columns=["month", "value", "series", "layer"])


@skill
def rolling_average(
    df: pd.DataFrame,
    *,
    month_col: str,
    value_col: str,
    den_col: str | None = None,
    group_col: str | None = None,
    window: int = 6,
) -> pd.DataFrame:
    """The N-month trailing-mean series — the smoothing primitive on its own.

    Where ``trend_series`` returns the actual + rolling overlay for a chart, this
    returns just the smoothed line: a DataFrame ``[month, value, series]`` with one
    row per month (the ``window``-month trailing mean, the same one growth and
    "latest" use). Use it when you want the smoothed values themselves — to read
    off a figure, difference two months, or drive a plain (non-overlay) chart.

    ``value = value_col / den_col`` when ``den_col`` is given, else ``value_col``.
    Months before the first non-null mean are dropped. Ungrouped calls label the
    single series with ``value_col``; grouped calls label each with its group.
    """
    grouped = _grouped(
        df,
        month_col=month_col,
        value_col=value_col,
        den_col=den_col,
        count_col=None,
        group_col=group_col,
    )
    rows: list[dict[str, Any]] = []
    for group, series in grouped.items():
        label = group if group != "_all" else value_col
        means = analytics.rolling_average(series, window)
        for point, mean in zip(series, means, strict=True):
            if mean is None:
                continue
            rows.append({"month": f"{point['month']}-01", "value": round(mean, 2), "series": label})
    return pd.DataFrame(rows, columns=["month", "value", "series"])


@skill
def growth_rate(
    df: pd.DataFrame,
    *,
    month_col: str,
    value_col: str,
    years: int,
    den_col: str | None = None,
    group_col: str | None = None,
) -> Any:
    """Percent growth over ``years``, measured on the 6-month rolling base.

    Returns a float (ungrouped) or ``{group: float|None}``. None when there
    isn't ``years`` of history or the base value is zero.
    """
    grouped = _grouped(
        df,
        month_col=month_col,
        value_col=value_col,
        den_col=den_col,
        count_col=None,
        group_col=group_col,
    )
    out = {g: analytics.growth_rate(s, years=years) for g, s in grouped.items()}
    return _maybe_single(out, group_col)


@skill
def top_growth(
    df: pd.DataFrame,
    *,
    month_col: str,
    value_col: str,
    group_col: str,
    years: int,
    den_col: str | None = None,
    n: int = 5,
    ascending: bool = False,
) -> pd.DataFrame:
    """Rank groups by % growth over ``years`` (6-month rolling base); top ``n``.

    The multi-entity ranker behind "top growth suburbs" questions: computes each
    group's growth the same way ``growth_rate`` does, drops groups without enough
    history, and returns a DataFrame ``[<group_col>, growth_pct]`` sorted
    descending (set ``ascending=True`` for the slowest movers). Feed the result
    straight to ``comparison_chart``.
    """
    grouped = _grouped(
        df,
        month_col=month_col,
        value_col=value_col,
        den_col=den_col,
        count_col=None,
        group_col=group_col,
    )
    rows = [
        {group_col: group, "growth_pct": analytics.growth_rate(series, years=years)}
        for group, series in grouped.items()
    ]
    out = pd.DataFrame(rows, columns=[group_col, "growth_pct"]).dropna(subset=["growth_pct"])
    out = out.sort_values("growth_pct", ascending=ascending).head(n)
    return out.reset_index(drop=True)


@skill
def latest_value(
    df: pd.DataFrame,
    *,
    month_col: str,
    value_col: str,
    den_col: str | None = None,
    group_col: str | None = None,
    smooth_window: int = 6,
) -> Any:
    """The latest value as a 6-month-smoothed figure, with its month.

    Returns ``{"value": float, "month": "YYYY-MM", "raw_value": float, "count": n}``
    (ungrouped) or ``{group: {...}}``. ``value`` is the smoothed figure at the
    newest month — the number to headline; ``raw_value`` is that month's actual
    unsmoothed figure and ``count`` its observation count, so a report can quote
    "the latest month itself" without re-deriving it (s49).
    """
    grouped = _grouped(
        df,
        month_col=month_col,
        value_col=value_col,
        den_col=den_col,
        count_col=None,
        group_col=group_col,
    )
    out: dict[str, Any] = {}
    for group, series in grouped.items():
        latest = analytics.latest_reliable(series, smooth_window=smooth_window)
        if latest is None or latest["value"] is None:
            out[group] = None
            continue
        point = series[latest["index"]]
        raw = point.get("value")
        out[group] = {
            "value": round(latest["value"], 2),
            "month": latest["month"],
            "raw_value": None if raw is None else round(float(raw), 2),
            "count": point.get("count"),
        }
    return _maybe_single(out, group_col)


@skill
def driver_analysis(
    df: pd.DataFrame,
    *,
    dimensions: list[str],
    value_col: str,
    den_col: str | None = None,
    top: int = 3,
) -> dict[str, Any]:
    """Rank which attribute most explains high/low values of a metric.

    The Insights-page primitive: for each candidate dimension (e.g.
    ``["property_type", "bedroom_band"]``), compute each level's metric
    (``sum(value_col)/sum(den_col)`` when ``den_col`` is given — the additive
    rule — else the mean of ``value_col``), its volume share, and its % delta
    from the overall metric. A dimension's **discrimination score** is the
    volume-weighted mean absolute % deviation of its level metrics from the
    overall — the "% contribution" method: bigger spread ⇒ stronger driver.

    Returns ``{"top_dimension", "overall", "ranked": [{dimension, score_pct,
    levels: [{level, value, share_pct, delta_pct}, ...]}, ...]}`` with levels
    sorted by value descending (``top`` + remainder collapsed is the caller's
    presentation choice; all levels are returned).
    """
    value = pd.to_numeric(df[value_col], errors="coerce")
    den = pd.to_numeric(df[den_col], errors="coerce") if den_col else None
    if den is not None:
        total_den = float(den.sum())
        overall = float(value.sum()) / total_den if total_den else None
    else:
        overall = float(value.mean()) if len(value) else None
    # Spelled out rather than `overall in (None, 0)` so the None is narrowed away
    # for everything below — the divisions at the heart of this function are only
    # safe because of this guard, and a type checker should be able to see that.
    if overall is None or overall == 0:
        return {"top_dimension": None, "overall": overall, "ranked": []}

    ranked: list[dict[str, Any]] = []
    for dim in dimensions:
        if dim not in df.columns:
            continue
        work = df.assign(__v=value, __d=(den if den is not None else 1.0))
        grouped = work.groupby(dim, dropna=True).agg(v=("__v", "sum"), d=("__d", "sum"))
        grouped = grouped[grouped["d"] > 0]
        if grouped.empty:
            continue
        # sum/sum is the additive rule with a denominator, the plain mean without.
        level_metric = grouped["v"] / grouped["d"]
        weights = grouped["d"] / float(grouped["d"].sum())
        deltas = (level_metric - overall) / overall * 100.0
        score = float((weights * deltas.abs()).sum())
        levels = [
            {
                "level": str(idx),
                "value": round(float(level_metric.loc[idx]), 2),
                "share_pct": round(float(weights.loc[idx]) * 100.0, 1),
                "delta_pct": round(float(deltas.loc[idx]), 1),
            }
            for idx in level_metric.sort_values(ascending=False).index
        ]
        ranked.append({"dimension": dim, "score_pct": round(score, 1), "levels": levels})

    ranked.sort(key=lambda r: r["score_pct"], reverse=True)
    if top > 0:
        ranked = ranked[:top]
    return {
        "top_dimension": ranked[0]["dimension"] if ranked else None,
        "overall": round(overall, 2),
        "ranked": ranked,
    }


@skill
def gross_yield(
    rent_df: pd.DataFrame,
    price_df: pd.DataFrame,
    *,
    key_cols: list[str],
    weekly_rent_col: str,
    price_col: str,
) -> float | None:
    """Annualised gross rental yield = (weekly rent × 52) / price, as a percent.

    Joins the two extracts on ``key_cols`` (e.g. ['postcode', 'month']), takes
    the latest common period, and weights nothing fancy — a straight annualised
    ratio. Returns None if the frames don't overlap. Rent has no suburb grain,
    so callers join on postcode + month (see suburbs-and-postcodes.md).
    """
    if rent_df.empty or price_df.empty:
        return None
    merged = rent_df.merge(price_df, on=key_cols, how="inner", suffixes=("_rent", "_price"))
    if merged.empty:
        return None
    rent = pd.to_numeric(merged[weekly_rent_col], errors="coerce")
    price = pd.to_numeric(merged[price_col], errors="coerce")
    annual = rent * 52.0
    ratio = (annual / price.where(price != 0)).dropna()
    if ratio.empty:
        return None
    return round(float(ratio.mean()) * 100.0, 2)


@skill
def period_metric(
    df: pd.DataFrame,
    *,
    period_col: str,
    value_col: str,
    den_col: str | None = None,
    weight_col: str | None = None,
    group_col: str | None = None,
    period: str | None = None,
    per_group_latest: bool = False,
    min_den: float = 0.0,
    decimals: int = 2,
) -> pd.DataFrame:
    """The weighted metric for a single period (default the latest), unsmoothed.

    The point-in-time counterpart to ``latest_value``. Where ``latest_value``
    headlines a 6-month-smoothed figure (the right number for a trend), this
    answers "what was it *in that month*" — the figure a KPI tile quotes when the
    question names a month, e.g. bond-weighted average weekly rent for the latest
    month (s49). No rolling base is applied; the period's own rows are combined.

    Weighting is mandatory and comes in two shapes — pass exactly one:

    * ``den_col``: ``value_col`` is a TOTAL, so the metric is a ratio of sums,
      ``sum(value_col) / sum(den_col)`` (total_weekly_rent / n_rented).
    * ``weight_col``: ``value_col`` is already a per-unit RATE, so the metric is
      the weighted mean ``sum(value_col * weight_col) / sum(weight_col)``
      (avg_weekly_rent weighted by n_rented).

    With neither, the plain mean of ``value_col`` is returned — correct only when
    every row already carries equal weight; prefer a weight wherever one exists,
    because a mean of per-row averages is not the population average.

    ``period=None`` selects the newest period in the frame (the same one for
    every group, so groups stay comparable); ``per_group_latest=True`` gives each
    group its own newest period instead; passing ``period="2026-05"`` (a
    ``YYYY-MM-DD`` value is truncated to its month) pins one explicitly.

    Returns a DataFrame ``[<group_col>?, <period_col>, value, numerator,
    denominator, n_rows]``, one row per group, sorted by value descending — the
    numerator/denominator/n_rows travel with the figure so a report can state its
    basis ("across 51 new bonds") without re-deriving it. Groups whose
    denominator falls under ``min_den`` are dropped, and a zero denominator is
    NULLIF'd away (the row is dropped) rather than emitted as inf/NaN into a
    tile. An empty or unmatched frame returns the same columns with no rows.
    """
    if den_col is not None and weight_col is not None:
        raise ValueError(
            "pass den_col (value_col is a total) or weight_col (value_col is a rate), not both"
        )
    out_cols = ([group_col] if group_col else []) + [
        period_col,
        "value",
        "numerator",
        "denominator",
        "n_rows",
    ]
    needed = [c for c in (period_col, value_col, den_col, weight_col, group_col) if c]
    if df.empty or any(c not in df.columns for c in needed):
        return pd.DataFrame(columns=out_cols)

    work = pd.DataFrame(index=df.index)
    key = df[period_col].astype(str).str.strip()
    # 'YYYY-MM-DD' and 'YYYY-MM' both key on the month, so a date-typed month
    # column and a string one select the same period.
    work["__period"] = key.mask(key.str.match(r"^\d{4}-\d{2}-\d{2}"), key.str.slice(0, 7))
    work["__group"] = df[group_col].astype(str) if group_col else "_all"
    value = pd.to_numeric(df[value_col], errors="coerce")
    if den_col is not None:
        work["__num"] = value
        work["__den"] = pd.to_numeric(df[den_col], errors="coerce")
    elif weight_col is not None:
        weight = pd.to_numeric(df[weight_col], errors="coerce")
        work["__num"] = value * weight
        work["__den"] = weight
    else:
        work["__num"] = value
        work["__den"] = 1.0
    work = work.dropna(subset=["__period", "__num", "__den"])
    work = work[work["__period"] != ""]
    if work.empty:
        return pd.DataFrame(columns=out_cols)

    if period is not None:
        want = str(period).strip()
        want = want[:7] if len(want) > 7 else want
        work = work[work["__period"] == want]
    elif per_group_latest:
        work = work[work["__period"] == work.groupby("__group")["__period"].transform("max")]
    else:
        work = work[work["__period"] == work["__period"].max()]
    if work.empty:
        return pd.DataFrame(columns=out_cols)

    agg = (
        work.groupby(["__group", "__period"], dropna=False)
        .agg(numerator=("__num", "sum"), denominator=("__den", "sum"), n_rows=("__num", "size"))
        .reset_index()
    )
    if min_den > 0:
        agg = agg[agg["denominator"] >= float(min_den)]
    # NULLIF-equivalent: no denominator, no metric — drop rather than emit inf.
    safe_den = agg["denominator"].replace(0, float("nan"))
    agg["value"] = (agg["numerator"] / safe_den).round(decimals)
    agg = agg.dropna(subset=["value"])
    if agg.empty:
        return pd.DataFrame(columns=out_cols)
    agg = agg.rename(columns={"__period": period_col, "__group": group_col or "__group"})
    agg["n_rows"] = agg["n_rows"].astype(int)
    agg = agg.sort_values("value", ascending=False).reset_index(drop=True)
    return agg[out_cols]
