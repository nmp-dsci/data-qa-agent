---
name: property-sales-overview
description: NSW property sales mart — table grain and the key columns to query.
applies_to: [sales, "sale price", house price, "sold", nsw_sales, property_sales, property price]
---

# Property sales (dataset nsw_sales)

## Primary building block: `marts.property_sales`
One row per **postcode + suburb + property_type + area_band + zoning + month**.
No precomputed growth.
- `postcode` (text) — join key to rent (with property_type, month)
- `suburb` (text) — real dimension; filter for one locality. Values are Title
  Case (e.g. 'Hornsby', 'Anabranch South') — resolve exact spellings with ONE
  lookup_values call (`pattern="A|B"` resolves several at once)
- `property_type` (text) — 'house' or 'unit'; no synthetic 'ALL' rows —
  [[domains/property-sales/property-types]]
- `area_band` (text) — cleaned lot-size band; part of the grain
- `zoning` (text) — planning zone or 'unknown'; part of the grain
- `month` (date) — first-of-month
- `total_sale_value` (numeric) — sum of sale_price that month
- `n_sold` (int) — count of sales that month
- `avg_sale_price` (numeric) — bucket-level average sale price
- `median_sale_price` (numeric) — bucket-level median sale price

Average price = `total_sale_value / nullif(n_sold, 0)` (composes across any window).
`total_sale_value` and `n_sold` are additive across postcode/suburb/property_type/
area/zoning buckets; bucket medians are NOT — do not sum medians.

## Other tables
- `staging.property_sales` — record grain, ~3M rows. Only for record-level questions
  (individual sales, addresses); always filter by postcode/month first.

## Reliability
Buckets are kept even when tiny. **Do not filter thin months out** of a trend or
series with a `WHERE n_sold >= N` clause — keep every month and let the 6-month
rolling average absorb the noise (the `rolling_average` / `latest_value` skills
apply the 6-month window). SELECT-only; RLS limits rows to the datasets the user
may access.
