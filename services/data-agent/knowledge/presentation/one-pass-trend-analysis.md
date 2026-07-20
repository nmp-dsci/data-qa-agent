---
name: one-pass-trend-analysis
description: Build a whole month-by-group trend answer in a single run_analysis pass.
applies_to: [trend, "over time", "vs", comparison, growth, "month by month", monthly]
---

# One-pass trend analysis

A "how has X moved over time" or "A vs B" question has a known shape. Plan the
whole answer before the first `run_analysis` call, then build it in **one pass**.
Re-running `run_analysis` to add one more object is the single largest source of
wasted turns on these questions — each retry re-executes the entire script.

## The shape

Nearly every trend question resolves to the same three things:

1. **One extract** — month, the grouping column(s), and the numerator/denominator
   columns. Get all of them in the first SQL, including any entity you are going
   to compare against.
2. **Derived series per group** — the rate is almost never a stored column.
   Compute it as `sum(numerator) / sum(denominator)` per month per group:
   - rent: `total_weekly_rent / nullif(n_rented, 0)`
   - sales: `total_sale_value / nullif(n_sold, 0)`
3. **The objects** — a trend chart over the series, plus the headline values
   (latest, and growth over the span). Build them all in the same script.

## Rules that save turns

- **Do not call `run_analysis` more than once** unless it raised an error. If an
  object is missing, you planned the script wrong — fix the script and re-run
  once, do not append a second call per object.
- **Do not call `describe_table` for a mart already covered by a knowledge
  page.** The columns are listed in
  [[domains/property-rent/overview]] and [[domains/property-sales/overview]];
  read the page instead. Describe a given table at most once per question — if
  you have already seen its columns, re-reading them cannot tell you anything
  new, and schema re-exploration is the largest avoidable cost on these
  questions. Decide every table you need before the first `describe_table`
  rather than discovering them one at a time.
- **Resolve entities in the extract, not with separate lookups.** A postcode or
  suburb named in the question can go straight into the `WHERE` clause; reach for
  `lookup_values` only when a spelling genuinely needs resolving.
- **Aggregate at the question's grain, not the mart's.** The marts are finer than
  most questions: rent is postcode × property_type × bedroom_band × month, sales
  is suburb × property_type × area_band × zoning × month. A question about "rent
  in 2077 vs 2076" is month × postcode — sum the other dimensions away rather
  than returning them, or the chart will show one arbitrary sub-segment.
- **Comparisons are one extract, not two.** Use `WHERE postcode IN ('2077','2076')`
  and group, rather than querying each entity separately.

## Worked shape

For "rent trends for postcode 2077 vs 2076": one extract of
`month, postcode, sum(total_weekly_rent), sum(n_rented)` grouped by month and
postcode; one script computing average weekly rent per month per postcode,
producing a trend object with a series per postcode plus latest-value and
growth headlines. Two entities, one extract, one analysis pass.
