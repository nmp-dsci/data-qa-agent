---
name: rent-bedrooms
description: Rent broken out by bedroom band; the right segmenter for units.
applies_to: [bedroom, "bedroom band", "by bedroom", studio, "1 bedroom", units profile]
---

# Rent by bedroom

`marts.property_rent` breaks rent down by **`bedroom_band`** ('0'..'4', '5+',
or 'unknown'). Grain: postcode + property_type + bedroom_band + month.

## Rules
- `bedroom_band` is part of the grain — there is no 'ALL' bedroom row. For an
  all-bedroom figure re-aggregate `total_weekly_rent` and `n_rented`.
- Use this for "rent by bedroom" questions and as the composition segmenter for
  **units** (the `profile_chart` skill) — bedrooms, not lot size, is what
  differentiates apartments.
- For a composition share (% of bonds that are 2-bedroom), sum `n_rented` per band
  and divide by the postcode total.
- Bedroom bands come from the bond record; 'unknown' is a real bucket — mention it
  if it is a large share rather than dropping it silently.
