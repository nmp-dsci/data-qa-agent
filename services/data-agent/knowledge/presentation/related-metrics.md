---
name: related-metrics
description: Which context metrics to add to headlines beyond the one asked for.
applies_to: [related, headline, context, rent, yield, price, enrich]
---

# Related metrics for headline enrichment

When a user asks about one metric, a good report shows adjacent metrics that give
context — as long as they are cheap to fetch and genuinely relevant. Mark these
`related=True` so the UI renders them as secondary (dashed) tiles.

## Property domain triads
For a named locality + property_type, price / rent / yield form a natural triad:
- Asked about **sale price** → add **median rent** and **gross yield** for the
  same locality + type + latest month.
- Asked about **rent** → add **median sale price** and **gross yield**.
- Asked about **yield** → add the **price** and **rent** it is composed from.

Compute yield with [[analysis/yield]]; resolve rent for a suburb via its postcode
([[domains/property-rent/overview]], [[domains/property-sales/suburbs-and-postcodes]]).

## Restraint
- Only add a related metric if the data to compute it is already accessible (the
  user's grants cover it) and one extra query gets it.
- Do not add related metrics to a simple count/lookup answer.
- Two related tiles per entity is plenty; do not flood the headline row.
