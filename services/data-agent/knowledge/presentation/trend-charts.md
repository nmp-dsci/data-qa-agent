---
name: trend-charts
description: How to build and present a time-series trend chart, incl. the actual + 6-month rolling-average overlay.
applies_to: [trend, "over time", "by month", "by year", timeseries, line chart, compare, vs]
---

# Trend charts

## Query shape
- Grain: one row per (entity, month). Alias the value column clearly
  (`avg_price`, `median_rent`). Pull the FULL requested window in one query.
- Multi-entity comparison (A vs B): ONE query with the entity as a column
  (`suburb`), never one query per entity — the chart colors by that column.
- Average price = `total_sale_value / nullif(n_sold, 0)`; average rent =
  `total_weekly_rent / nullif(n_rented, 0)` (see the domain overviews).
- **Keep every month — return the raw monthly series.** Never add a
  `WHERE n_sold >= N` / `n_rented >= N` filter to a trend query; the 6-month
  rolling average (below) is what absorbs thin-month noise, not a row filter
  ([[analysis/rolling-averages]], [[analysis/latest-month]]).

## Presentation
- Show TWO layers per entity: the **actual** monthly series (thin, faint) and a
  **6-month rolling average** (bold, solid). Monthly buckets are noisy; the
  smoothed line carries the trend. Build the series with `compute_trend` — it
  returns both layers (`layer` = `"actual"` and `"6-mo avg"`) already keyed by
  entity (`series`), ready to plot with `make_chart` on its `chart_data_ref`.
- The overlay **styling is applied server-side**: for any `compute_trend` series
  (rows carry a `layer` field), `make_chart` automatically colours by entity
  (`series`) and separates the two layers by opacity + line weight — faint thin
  actual under a bold solid average. You do NOT author the opacity/strokeWidth/
  colour yourself.

### make_chart for a trend overlay
Pass `mark: "line"` and a minimal `encoding` — just the axes (the server adds the
colour/opacity/weight):

```json
{
  "x": {"field": "month", "type": "temporal", "title": null},
  "y": {"field": "value", "type": "quantitative", "title": "avg price",
        "axis": {"format": "$,.0f"}}
}
```

- Optionally add a `tooltip` array; it is preserved. You may add a Vega-Lite
  interval `params` selection bound to `scales` for pan/zoom. The chart data and
  the actual-vs-average styling are both spliced server-side.

## Related figures
- Headline growth and "latest" values also use the **6-month** rolling base, so
  the numbers and the chart line agree ([[analysis/growth-rates]],
  [[analysis/latest-month]]).

## Learned pitfalls
- Suburb values in the marts are UPPERCASE ('HORNSBY'); match case or use ILIKE —
  a mixed-case literal silently returns zero rows.
- Keep `property_type` explicit for house-vs-unit questions; 'ALL' blends both and
  understates a house-only trend ([[domains/property-sales/property-types]]).
