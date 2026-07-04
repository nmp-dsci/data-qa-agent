---
name: property-types
description: house vs unit vs 'ALL' — when each matters and when 'ALL' misleads.
applies_to: [house, unit, apartment, property type, "houses vs units", dwelling]
---

# Property types

`property_type` in the sales/rent marts is one of `'house'`, `'unit'`, or `'ALL'`
(a pre-blended row across both).

## Rules
- If the user names a type ("houses", "apartments"), filter to it explicitly. Do
  NOT use 'ALL' for a type-specific question — 'ALL' blends houses and units and
  will understate a house-only trend (houses are pricier) or overstate a unit one.
- 'unit' covers apartments / strata dwellings. For units, land/lot size is not
  meaningful — profile them by **bedroom band**, not sqm
  ([[presentation/profile-sections]], [[domains/property-rent/bedrooms]]).
- Only use 'ALL' when the user genuinely wants the whole market blended, or as a
  denominator for share calculations.
- When comparing house vs unit, keep them as two series (colour by type), same as
  comparing two suburbs.
