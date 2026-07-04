# Knowledge index (auto-generated — run `python -m agent.knowledge`)

Do not edit by hand; regenerate from page frontmatter.

```
[analysis]
  growth-rates — How to compute N-year growth rates off a 6-month rolling base, not raw months.
  latest-month — Report "latest" as the 6-month rolling value at the newest month — smooth, don't filter data out.
  rolling-averages — Smooth every trend with a 6-month rolling average — the one window for both the chart line and growth.
  yield — Gross rental yield = annualised rent / price; how to compute and weight it.
[domains]
  _template-overview — Scaffold for a new dataset domain — copy this folder to add one.
  property-rent-overview — NSW rent marts — postcode-grained, no suburb; key columns.
  property-sales-overview — NSW property sales marts — tables, grain, and the key columns to query.
  property-types — house vs unit vs 'ALL' — when each matters and when 'ALL' misleads.
  rent-bedrooms — Rent broken out by bedroom band; the right segmenter for units.
  sales-segments — Lot-size band and planning zone breakdowns for house sales.
  suburbs-and-postcodes — Suburb<->postcode mapping; sales have suburb, rent does not.
[presentation]
  comparison-charts — Bar/grouped-bar charts for comparing metrics across entities inside insight cards.
  profile-sections — Composition comparisons between entities; segmenter differs per domain.
  related-metrics — Which context metrics to add to headlines beyond the one asked for.
  report-structure — The sections of an insight report, their order, and when to skip one.
  trend-charts — How to build and present a time-series trend chart, incl. the actual + 6-month rolling-average overlay.
  what-makes-an-insight — What distinguishes a useful insight from a restated data row.
```
