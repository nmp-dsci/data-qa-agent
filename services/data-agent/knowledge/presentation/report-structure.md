---
name: report-structure
description: The sections of an insight report, their order, and when to skip one.
applies_to: [report, structure, sections, layout, summary, headline, insight]
---

# Report structure

An analytical answer is returned as a structured `InsightReport`, not free prose.
Compose these sections, in this order:

1. **summary** — one sentence that directly answers the question. Lead with the
   finding, not the method ("Normanhurst has out-grown Hornsby at every horizon",
   not "I queried the sales mart").
2. **headlines** — 3-6 stat tiles. Always include the metric the user asked for,
   at the most recent reliable month. Add *related* metrics for context — see
   [[presentation/related-metrics]] (mark those `related=True`).
   For a head-to-head comparison ("A vs B"), lay the tiles out so the comparison
   is legible at a glance: one tile per (entity × metric), grouped so BOTH
   entities for the same metric sit adjacent (A latest, B latest, A 10yr, B 10yr…),
   each label prefixed with the entity ("Normanhurst · 10yr growth"). Keep every
   directly-compared metric `related=False` for BOTH entities — never mark one
   suburb's metric primary and the other's related, which splits the pair into
   different rows and breaks the visual comparison. `related=True` is only for a
   genuine context metric neither side was asked about. When one number is the
   whole point of the comparison, add an explicit gap tile ("Growth gap · 10yr",
   value "+10.7 pts") so the reader doesn't have to subtract in their head.
3. **insights** — 2-4 claims, each stating something the raw table does not
   ([[presentation/what-makes-an-insight]]). Every insight cites the query that
   backs it (`query_refs`). Add an inline `chart` when a comparison reads better
   visually than as a sentence ([[presentation/comparison-charts]]).
4. **profiles** — optional. Composition comparisons between the entities
   ([[presentation/profile-sections]]) — e.g. lot-size mix. Only when the entities
   differ structurally in a way that explains the headline.
5. **queries** — every successful `run_sql` call, numbered Q1, Q2 … so the user
   can copy it or open it in the SQL editor. Never discard an intermediate query.

## When to skip a section
- Simple lookups ("how many suburbs?") need only `summary` + one headline — no
  insights, no profile, no chart. Do not pad a one-number answer into a report.
- Skip `profiles` unless composition actually explains something in the headline.
- Skip a chart when there is nothing to compare over time or across entities.

## Never
- Never state a number in prose that no query in `queries` returned.
- Never restate a row as an "insight" (see [[presentation/what-makes-an-insight]]).
