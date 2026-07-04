---
name: comparison-charts
description: Bar/grouped-bar charts for comparing metrics across entities inside insight cards.
applies_to: [bar chart, comparison, ranking, top-n, grouped bar, compare metric]
---

# Comparison charts

Use a bar chart when the story is "A vs B on some metric" and there is no time
axis — it reads faster than a sentence. These often ride *inside* an insight card
(`Insight.chart`) rather than as the report's main chart.

## Shapes
- **Grouped bars** — two entities across a few measures (e.g. 5yr vs 10yr growth
  for Normanhurst and Hornsby). Group by measure, colour by entity.
- **Ranked bars** — top-N entities by one metric; cap N (10-15) and say so; sort
  descending. Fold the long tail into "other" rather than drawing 200 bars.

## Rules
- An inline insight/profile chart must be a DIFFERENT chart from the report's main
  chart. Never set an insight's `chart` to the same ref as `main_chart_ref` — the
  main chart is already shown once at the top, so reusing it renders the identical
  chart twice. If an insight is just narrating the main trend, cite it in the text
  and leave the insight's `chart` empty; only attach a chart when it adds a new view
  (e.g. a grouped bar of the growth figures).
- Colour by entity, consistent with the report's other charts (same suburb = same
  colour everywhere).
- Label the bar ends with the value; keep the axis for the ones you did not label.
- Every comparison chart is backed by a query — cite it in the insight's
  `query_refs`. The chart's data values are spliced server-side; you only author
  the mark/encoding.
