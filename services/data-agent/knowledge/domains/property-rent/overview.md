---
name: property-rent-overview
description: NSW rent marts — postcode-grained, no suburb; key columns.
applies_to: [rent, "weekly rent", rental, bond, nsw_rent, mart_rent_summary]
---

# Property rent (dataset nsw_rent)

## Primary building block: `marts.mart_rent_summary`
One row per **postcode + property_type + month** — there is **no suburb column**
(the rental-bond source has no locality). No precomputed growth.
- `postcode` (text), `property_type` (text: 'house'/'unit'/'ALL'), `month` (date)
- `total_weekly_rent` (numeric) — sum of weekly_rent that month
- `n_rented` (int) — count of bonds that month
- `median_rent` (numeric) — median weekly rent AUD that month

Average rent = `total_weekly_rent / nullif(n_rented, 0)`.

## Resolving a suburb
Rent has no suburb, so a "rent in <suburb>" question must first map the suburb to
its postcode(s) via `staging.int_postcode_geo`
([[domains/property-sales/suburbs-and-postcodes]]), then query rent by postcode.

## Other tables
- `marts.mart_rent_by_bedroom` — rent broken out by bedroom band
  ([[domains/property-rent/bedrooms]]).
- `marts.mart_property_yield` — sales+rent pre-joined on (postcode, property_type,
  month); compute yield from it (the `gross_yield` skill).
- `staging.stg_rent` — record grain (~3M rows); only for record-level questions,
  always filtered by postcode/month.

## Reliability
Bond counts per postcode/month can be thin. **Do not filter thin months out** of a
trend with a `WHERE n_rented >= N` clause — keep every month and let the 6-month
rolling average absorb the noise (the `rolling_average` / `latest_value` skills
apply the 6-month window).
