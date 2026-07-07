# s07 · UI refresh — chart-library worked example

This folder is the **iterable source of truth** for the core decision in
[`.lavish/s07_ui-refresh-plan.html`](../../.lavish/s07_ui-refresh-plan.html) §03: *which charting
library should the refreshed frontend use?*

**Decision (updated):** go **all-in on visx (react-d3)** — the frontend authors every chart, agent-authored
Vega-Lite is retired, and Recharts is dropped. This folder backs the **worked example**: the same real data
rendered as a **visx composition** — latest-number tiles with growth beside a bar chart — showing the control
visx gives that a chart-spec library can't.

## The pinned question

> "can you show me postcode for hornsby suburb weekly rent by bedrooms"

**Nuance this question exposes:** the rent mart (`marts.property_rent`) is at **postcode grain** — the rent
source has no suburb. So "Hornsby suburb" resolves to **postcode 2077**, and the honest answer is
*weekly rent by `bedroom_band` for postcode 2077*. Good stress-test for the agent and the chart.

## Data files (real, from the running DB)

| File | What | Chart it drives |
|------|------|-----------------|
| `rent_by_bedroom.json` | Median weekly rent × `bedroom_band` × `property_type`, postcode 2077, months ≥ 2024-01 (14 rows) | **grouped bar comparison** — the primary answer |
| `rent_trend_units.json` | Monthly median weekly rent for units, 1/2/3-bed, postcode 2077, from 2023-01 (123 rows) | **trend line** — the app's "today" style |

Each file is self-describing: `question`, `resolved`, `source`, `sql`, `primary_chart` (encoding), `columns`, `rows`.
Regenerate with the `sql` field against `marts.property_rent` (DB on `localhost:5434`, db `dataqa`).

## The target rendering (visx)

A single **visx composition** driven by these rows:

- **Row 1 · col 1** — latest-number KPI tiles with a secondary growth rate (e.g. `2br unit $671/wk ▲ +6.1% YoY`).
- **Row 1 · col 2** — grouped **bar chart**, median weekly rent by `bedroom_band`, house vs unit.
- Later: a trend line (from `rent_trend_units.json`) as a second view-page.

The agent stops authoring chart specs; it emits the governed rows + a light chart intent (`bar` / `trend`),
and the frontend's visx components render them inside the answer's layout template.

## How to iterate

1. Edit / regenerate the JSON here (SQL is in each file's `sql` field; DB on `localhost:5434`, db `dataqa`).
2. Build the visx components (Bar, Line, Axis, Legend, Tile) in a small React sandbox that reads these rows.
3. Screenshot the composition into the plan artifact's §03 worked example (replacing the hand-built SVG mock).
4. Reuse the same components for the SQL editor's "result → chart" and the Admin KPI strip.

The `.lavish` HTML currently shows a **hand-built SVG stand-in** of the visx composition from these exact values —
swap it for the real sandbox screenshot once the components exist.
