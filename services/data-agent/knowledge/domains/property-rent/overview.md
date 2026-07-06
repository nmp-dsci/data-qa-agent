---
name: property-rent-overview
description: NSW rent mart — postcode-grained, no suburb; key columns.
applies_to: [rent, "weekly rent", rental, bond, nsw_rent, property_rent]
---

# Property rent (dataset nsw_rent)

## Primary building block: `marts.property_rent`
One row per **postcode + property_type + bedroom_band + month** — there is
**no suburb column** (the rental-bond source has no locality). No precomputed growth.
- `postcode` (text), `property_type` (text: 'house'/'unit'), `bedroom_band` (text), `month` (date)
- `total_weekly_rent` (numeric) — sum of weekly_rent that month
- `n_rented` (int) — count of bonds that month
- `avg_weekly_rent` (numeric) — bucket-level average weekly rent
- `median_weekly_rent` (numeric) — bucket-level median weekly rent

Average rent = `total_weekly_rent / nullif(n_rented, 0)`.

## Resolving a suburb
Rent has no suburb, so a "rent in <suburb>" question must first map the suburb to
its postcode(s) via `staging.int_postcode_geo`
([[domains/property-sales/suburbs-and-postcodes]]), then query rent by postcode.

## Other tables
- `staging.property_rent` — record grain (~3M rows); only for record-level questions,
  always filtered by postcode/month.

## Reliability
Bond counts per postcode/month can be thin. **Do not filter thin months out** of a
trend with a `WHERE n_rented >= N` clause — keep every month and let the 6-month
rolling average absorb the noise (the `rolling_average` / `latest_value` skills
apply the 6-month window).
