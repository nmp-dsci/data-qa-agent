---
name: when-to-visualise
description: Chart-selection guide for matching a question and data shape to a chart skill.
applies_to: [chart, visualise, visualize, graph, trend, compare, distribution, histogram, profile, dual axis]
---

# When to visualise

Use the simplest chart that matches the analytical shape. Do not add a chart for
a single KPI unless it clarifies a comparison or trend.

## Selection guide
- One metric over time: `trend_chart`.
- One metric over time with raw + smoothed values: `trend_chart` from `trend_series`.
- Two metrics over the same time/entity axis with different scales: `dual_axis_chart`.
- Compare entities or ranked groups: `comparison_chart`.
- Compare entity composition or mix: `profile_chart`.
- Spread, outliers, or value distribution: `distribution_chart`.
- A single latest value: headline tile, no chart.

## Guardrails
- Chart skills receive real extracted rows; do not invent `data.values`.
- Prefer chart skills over hand-authored Vega-Lite.
- If the question asks for a chart type no skill supports, call `skill_gap`.
