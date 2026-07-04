---
name: yield
description: Gross rental yield = annualised rent / price; how to compute and weight it.
applies_to: [yield, "rental yield", gross yield, return, "rent to price"]
---

# Rental yield

Gross yield ties the two datasets together. There is no precomputed yield column —
compute it.

## Formula
- Point estimate: `gross_yield_pct = (median_rent * 52 / median_price) * 100`.
- Volume-weighted variant (steadier): use
  `(total_weekly_rent / n_rented) * 52 / (total_sale_value / n_sold) * 100`.
- Alias the result `gross_yield_pct` consistently.

## Gotchas
- Rent has **no suburb** — it is postcode-level. Resolve a suburb to its
  postcode(s) first ([[domains/property-sales/suburbs-and-postcodes]]); a
  postcode-level rent repeated across suburbs must not be summed across them.
- Match `property_type` on both sides (house rent vs house price), unless the
  question is deliberately blended ('ALL').
- Use reliable months on both legs ([[analysis/latest-month]]) — a thin rent month
  produces a nonsense yield.
- Yield needs grants on BOTH nsw_sales and nsw_rent; a user with only one gets no
  yield (RLS returns no rows for the missing side) — say so rather than inventing.
