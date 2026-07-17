"""Explore capability grounding for the data-agent (s19, Phase E).

The Explore feature (backend-api `app/explore/*`) exposes three governed datasets
and a cohort-profile capability. The agent leverages the SAME marts through its
normal SELECT-only, RLS-scoped executor — the yield mart and the geo dimension are
tagged ``agent_queryable``, so their full per-column descriptions already reach
`get_schema()` via the dbt manifest. This module adds the two things the manifest
can't express on its own:

* the fact that the three Explore datasets exist and which mart backs each, and
* the profile-comparison *pattern* (a Target cohort vs a Comparison cohort, then
  rank each predictor's segment deltas) the agent should run in SQL when asked
  "what drove / explains the change in X".

`EXPLORE_DATASETS` mirrors the backend manifest's slugs + backing tables; the
cross-service test `tests/test_explore_agent_sync.py` asserts they stay in step,
so agent capability and the Explore UI can't drift.
"""

from __future__ import annotations

GEO_DIM = "marts.dim_postcode_geo"

# slug -> (backing mart table, headline metric, geo key)
EXPLORE_DATASETS: dict[str, dict[str, str]] = {
    "nsw_sales": {"table": "marts.property_sales", "headline": "avg_sale_price", "geo": "postcode"},
    "nsw_rent": {"table": "marts.property_rent", "headline": "avg_weekly_rent", "geo": "postcode"},
    "nsw_yield": {
        "table": "marts.property_yield",
        "headline": "gross_yield_pct",
        "geo": "postcode",
    },
}


def explore_grounding() -> str:
    """A compact grounding block naming the Explore datasets and the profile
    pattern, appended to the agent's schema doc."""
    lines = [
        "Explore datasets (governed, RLS-scoped) and their marts:",
    ]
    for slug, meta in EXPLORE_DATASETS.items():
        lines.append(f"  {slug} -> {meta['table']} (headline metric {meta['headline']})")
    lines.append(
        "When asked what DROVE or EXPLAINS a change (e.g. 'what drove rent growth in "
        "Newcastle FY22 vs FY21?'), run a profile comparison: pick the response metric, "
        "define a Target cohort and a Comparison cohort as WHERE filters, compute the "
        "metric for each, then for every breakdown dimension (property_type, bedroom_band, "
        "postcode, SA3/SA4 region via " + GEO_DIM + ", …) rank the segment deltas "
        "(target minus comparison) to surface the strongest movers. Roll a postcode up to "
        "a region by JOINing " + GEO_DIM + " on postcode. Financial year ending June: "
        "months Jul..Jun map to the later year (FY2022 = 2021-07..2022-06). "
        "DEFAULTING SKILL: when a comparison period is not stated, default it to the "
        "prior period of the same kind — FY-on-FY (FY2022 vs FY2021) or CY-on-CY "
        "(2023 vs 2022) — holding every other filter equal between the two cohorts. "
        "PRESENTATION: rank the segment deltas as a table — "
        "skills.data_table(df, columns=[...], variant='ranked', bar_key='delta') passed to "
        "build_report(table=...) — the same ranked-uplift table the Explore Profile tool "
        "shows, so profile answers read identically in chat and Explore."
    )
    return "\n".join(lines)
