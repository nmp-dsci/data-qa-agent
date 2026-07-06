---
name: suburbs-and-postcodes
description: Suburb<->postcode mapping; sales have suburb, rent does not.
applies_to: [suburb, postcode, locality, "suburb name", geo, bridge]
---

# Suburbs and postcodes

- **Sales** carry a real `suburb` dimension (from the sale record). Filter by suburb
  for one locality. But postcode<->suburb is **not 1:1** — to get a postcode total,
  SUM `total_sale_value` / `n_sold` across that postcode's suburbs (additive);
  bucket medians are not additive.
- **Rent has NO suburb** (the source has no locality). For a rent-by-suburb
  question, resolve the suburb to its postcode(s) first via
  `staging.int_postcode_geo` (`WHERE suburb ILIKE '%name%'`), then query rent by
  postcode ([[domains/property-rent/overview]]).
- Join sales<->rent on **(postcode, property_type, month)**, never on suburb.

## Gotchas
- Suburb values are UPPERCASE in the marts ('HORNSBY', 'NORMANHURST'). Use the exact
  case or ILIKE — a mixed-case literal returns zero rows silently.
- A suburb may map to several postcodes and vice versa; decide whether the question
  is about the suburb (use the suburb dimension on sales) or the postcode (aggregate).
