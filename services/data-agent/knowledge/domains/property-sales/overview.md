---
name: property-sales-overview
description: NSW property sales marts — tables, grain, and the key columns to query.
applies_to: [sales, "sale price", house price, "sold", nsw_sales, mart_sales_summary, property price]
---

# Property sales (dataset nsw_sales)

## Primary building block: `marts.mart_sales_summary`
One row per **postcode + suburb + property_type + month**. No precomputed growth.
- `postcode` (text) — join key to rent (with property_type, month)
- `suburb` (text) — real dimension; filter for one locality (UPPERCASE values)
- `property_type` (text) — 'house', 'unit', or 'ALL' (blended) —
  [[domains/property-sales/property-types]]
- `month` (date) — first-of-month
- `total_sale_value` (numeric) — sum of sale_price that month
- `n_sold` (int) — count of sales that month
- `median_price` (numeric) — median sale price AUD that month

Average price = `total_sale_value / nullif(n_sold, 0)` (composes across any window).
`total_sale_value` and `n_sold` are additive across a postcode's suburbs;
`median_price` is NOT — do not sum medians.

## Other tables
- `marts.mart_sales_by_segment` — same, broken out by `area_band` + `zoning`
  ([[domains/property-sales/segments]]). Those dims are part of the grain (no 'ALL'
  row) — never SUM across them.
- `staging.stg_sales` — record grain, ~3M rows. Only for record-level questions
  (individual sales, addresses); always filter by postcode/month first.

## Reliability
Buckets are kept even when tiny. **Do not filter thin months out** of a trend or
series with a `WHERE n_sold >= N` clause — keep every month and let the 6-month
rolling average absorb the noise (the `rolling_average` / `latest_value` skills
apply the 6-month window). SELECT-only; RLS limits rows to the datasets the user
may access.
