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
) -> pd.DataFrame:
    """Long-form trend rows for charting: actual + N-month rolling per series.

    Returns a DataFrame with columns ``month, value, series, layer`` — the exact
    shape ``trend_chart`` expects and the same actual+rolling overlay the app
    renders today. Keeps every month; the rolling average absorbs noise.

    ``value = value_col / den_col`` when ``den_col`` is given (e.g.
    total_sale_value / n_sold → average price), else ``value_col`` directly.
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
        for row in analytics.chart_series(series, rolling_window=window):
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

    Returns ``{"value": float, "month": "YYYY-MM"}`` (ungrouped) or
    ``{group: {...}}``. "Latest" is the smoothed value at the newest month, not
    a raw single month.
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
        out[group] = (
            None
            if latest is None or latest["value"] is None
            else {"value": round(latest["value"], 2), "month": latest["month"]}
        )
    return _maybe_single(out, group_col)


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
