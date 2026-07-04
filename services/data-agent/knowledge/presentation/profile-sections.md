---
name: profile-sections
description: Composition comparisons between entities; segmenter differs per domain.
applies_to: [profile, composition, mix, breakdown, segment, sqm, bedroom, zoning]
---

# Profile sections

A profile compares the *composition* of two or more entities to explain a headline
— e.g. "Hornsby's houses skew to smaller lots, which explains part of the price
gap." Render it as stacked/grouped composition bars (shares summing to 100%).

## Choose the segmenter by domain and property_type
The dimension you break composition down by must be meaningful for the thing asked
about:
- **Houses** → lot-size band (`area_band`) or planning `zoning`
  ([[domains/property-sales/segments]]). Lot size drives house value.
- **Units / apartments** → **bedroom band**, NOT lot size — sqm/land area is not
  meaningful for a strata apartment ([[domains/property-rent/bedrooms]]). Never
  show a sqm-band profile for units.
- A different domain (e.g. demographics) supplies its own segmenter via its
  domain pages.

## When to include a profile
- Only when the entities differ structurally in a way that *explains* the headline.
- Skip it when composition is similar or irrelevant to the question.
- Always back it with its own query (cite `query_refs`); state the "so what" in the
  body, not just the percentages.
