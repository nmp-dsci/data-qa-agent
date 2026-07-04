---
name: latest-month
description: Report "latest" as the 6-month rolling value at the newest month — smooth, don't filter data out.
applies_to: [latest, current, "most recent", "as of", now, "this month"]
---

# "Latest" / "current" values

The single most recent month is often thin — a suburb might have 1-2 sales in the
newest bucket — so a raw single-month figure is not trustworthy on its own. The
fix is to **smooth, not to drop data.**

## Method
- Keep every month in the series. **Do not** add a `WHERE n_sold >= N` /
  `n_rented >= N` filter and do not `filter month = max(month)` and stop —
  filtering thin months out distorts the series and the chart
  ([[analysis/rolling-averages]], [[domains/property-sales/overview]]).
- For a headline "latest average", report the **6-month rolling average** at the
  most recent month with data, not the single-month figure. The `latest_reliable`
  / `compute_trend` tools do this: they return the 6-month-smoothed value at the
  newest month.
- State the month the figure is as-of ("6-mo rolling, Mar 2026") so the user knows
  it is a smoothed trailing figure, not a single raw month.

## Rules
- Present the smoothed (6-month rolling) figure as "current", never a lone thin
  month's raw value.
- If the series itself is very short (< 6 months of history), say the latest
  figure is provisional rather than quoting false precision.
