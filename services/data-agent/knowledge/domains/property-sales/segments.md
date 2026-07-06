---
name: sales-segments
description: Lot-size band and planning zone breakdowns for house sales.
applies_to: [sqm, "lot size", area band, zoning, "planning zone", segment, composition]
---

# Sales segments — lot size & zoning

`marts.property_sales` includes **`area_band`** (lot-size band:
'<400', '400-700', ... '5000+', 'unknown') and **`zoning`** (NSW zone code e.g. R2,
RU5, 'unknown'). Grain: postcode + suburb + property_type + area_band + zoning +
month.

## Rules
- `area_band` and `zoning` are part of the grain — there is no 'ALL' row for them.
  For an all-segment figure re-aggregate `total_sale_value` and `n_sold`.
- Use this table for "price by lot size" / "price by zone" questions and for
  **house** composition profiles (the `profile_chart` skill).
- Lot-size bands only make sense for **houses**. For units, profile by bedroom
  ([[domains/property-rent/bedrooms]]) — a strata apartment's land area is not
  meaningful.
- To compute a composition share (e.g. % of house sales under 400 m²), sum `n_sold`
  per band and divide by the suburb total; do not use bucket medians for shares.
