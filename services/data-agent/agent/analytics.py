"""Deterministic analytics — pure Python over query rows, never LLM arithmetic.

The agent computes headline figures (rolling averages, growth rates, latest
reliable values) by calling these functions on the rows a query already
returned, rather than doing the maths itself. Same rows in → same numbers out,
so two runs of the same question cannot disagree on a headline. No DB, no
network, no third-party deps — unit-testable in isolation (see tests).

A *series* is a list of points sorted ascending by month:
    [{"month": "2010-01", "value": 1234.0, "count": 12}, ...]
where ``value`` is the metric (e.g. avg price) and ``count`` its underlying
sample size (e.g. n_sold), used for reliability floors.
"""

from __future__ import annotations

from typing import Any

Point = dict[str, Any]
Series = list[Point]


def build_series(
    columns: list[str],
    rows: list[list[Any]],
    *,
    month_col: str,
    value_col: str,
    den_col: str | None = None,
    count_col: str | None = None,
    group_col: str | None = None,
) -> dict[str, Series]:
    """Turn raw query rows into one month-sorted series per group.

    ``value`` = value_col / den_col when den_col is given (e.g.
    total_sale_value / n_sold), else value_col directly. ``count`` = count_col
    (or den_col) when available, else 0.
    """
    idx = {c: i for i, c in enumerate(columns)}
    for required in (month_col, value_col):
        if required not in idx:
            raise KeyError(f"column {required!r} not in {columns}")
    count_key = count_col or den_col
    grouped: dict[str, Series] = {}
    for row in rows:
        group = str(row[idx[group_col]]) if group_col and group_col in idx else "_all"
        raw_value = _to_float(row[idx[value_col]])
        if den_col and den_col in idx:
            den = _to_float(row[idx[den_col]])
            if raw_value is not None and den is not None and den != 0:
                value = raw_value / den
            else:
                value = None
        else:
            value = raw_value
        count = _to_float(row[idx[count_key]]) if count_key and count_key in idx else 0.0
        month = _month_str(row[idx[month_col]])
        grouped.setdefault(group, []).append(
            {"month": month, "value": value, "count": count or 0.0}
        )
    for series in grouped.values():
        series.sort(key=lambda p: p["month"])
    return grouped


def rolling_average(series: Series, window: int, *, key: str = "value") -> list[float | None]:
    """Trailing mean of the last ``window`` non-null values at each position."""
    values = [p.get(key) for p in series]
    out: list[float | None] = []
    for i in range(len(values)):
        window_vals = [v for v in values[max(0, i - window + 1) : i + 1] if v is not None]
        out.append(sum(window_vals) / len(window_vals) if window_vals else None)
    return out


def latest_reliable_index(series: Series, min_count: float = 0.0) -> int | None:
    """Index of the newest point whose count clears the floor (else newest non-null).

    The floor defaults to 0 — we keep every month and lean on the rolling
    average to absorb thin-month noise, rather than dropping data. A caller can
    still pass a positive ``min_count`` where a raw single-month figure must be
    trustworthy, but the trend/series path never filters months out.
    """
    for i in range(len(series) - 1, -1, -1):
        if series[i].get("value") is not None and series[i].get("count", 0.0) >= min_count:
            return i
    for i in range(len(series) - 1, -1, -1):
        if series[i].get("value") is not None:
            return i
    return None


def latest_reliable(
    series: Series, *, min_count: float = 0.0, smooth_window: int = 6
) -> dict[str, Any] | None:
    """The latest value as a 6-month-smoothed (rolling) figure + its month."""
    idx = latest_reliable_index(series, min_count)
    if idx is None:
        return None
    smoothed = rolling_average(series, smooth_window)
    value = smoothed[idx]
    if value is None:
        value = series[idx].get("value")
    return {"month": series[idx]["month"], "value": value, "index": idx}


def growth_rate(
    series: Series, *, years: int, min_count: float = 0.0, base_window: int = 6
) -> float | None:
    """Percent growth over ``years``, measured on the 6-month rolling base.

    Compares the 6-month-smoothed value at the latest month against the smoothed
    value ``years*12`` months earlier. A 6-month base stabilises the noisy
    monthly buckets while staying responsive. Returns None when there isn't
    enough history or the base is zero.
    """
    end = latest_reliable_index(series, min_count)
    if end is None:
        return None
    start = end - years * 12
    if start < 0:
        return None
    smoothed = rolling_average(series, base_window)
    start_val, end_val = smoothed[start], smoothed[end]
    if start_val is None or start_val == 0 or end_val is None:
        return None
    return round((end_val - start_val) / start_val * 100.0, 1)


def chart_series(series: Series, *, rolling_window: int = 6) -> list[dict[str, Any]]:
    """Rows for a trend chart: actual + rolling-average, long-form for color-by-series."""
    smoothed = rolling_average(series, rolling_window)
    out: list[dict[str, Any]] = []
    for point, smooth in zip(series, smoothed, strict=True):
        if point.get("value") is not None:
            out.append(
                {"month": point["month"], "value": round(point["value"], 2), "layer": "actual"}
            )
        if smooth is not None:
            out.append(
                {
                    "month": point["month"],
                    "value": round(smooth, 2),
                    "layer": f"{rolling_window}-mo avg",
                }
            )
    return out


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _month_str(value: Any) -> str:
    s = str(value)
    # Normalise 'YYYY-MM-DD...' to 'YYYY-MM' for stable monthly buckets/sorting.
    if len(s) >= 7 and s[4] == "-":
        return s[:7]
    return s
