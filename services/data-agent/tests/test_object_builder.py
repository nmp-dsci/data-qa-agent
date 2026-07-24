"""Deterministic Presentation Object builder (s18).

Covers the guarantees behind "build a named object by picking columns/skills, no
LLM, and get a report-ready combo":

* ``build_object_code`` for a compare spec runs in the sandbox and its
  ``main_chart`` lifts to a ``compare`` object with the requested encodings and
  ONE row per (dimension, group) — the grain the house combo renderer needs;
* ``needed_columns`` reads every source column the spec touches;
* ``canonical_extract_sql`` emits a grain-level extract, using an explicit filter
  when given and otherwise carrying the golden's suburb/property_type filters;
* ``element_id_for`` slugs the object name into a stable link id.

No DB or LLM: the sandbox runs over an in-memory frame, so these are fast and
deterministic.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from agent.object_builder import (
    build_object_code,
    canonical_extract_sql,
    element_id_for,
    extract_grain,
    name_from_instruction,
    needed_columns,
    profile_for,
    slug,
)
from agent.pages import chart_object_from_spec
from agent.sandbox import run_code

_SPEC = {
    "grain": ["month", "suburb", "area_band"],
    "dimension": "area_band",
    "group": "suburb",
    "bar_measure": {"label": "sales_volume", "source": "n_sold", "agg": "sum", "months": 12},
    "line_measure": {
        "label": "avg_sale_price",
        "num": "total_sale_value",
        "den": "n_sold",
        "months": 6,
    },
    "months": 12,
    "title": "line-bar-sale-volume",
}


def _frame() -> pd.DataFrame:
    rows = []
    months = [f"2024-{m:02d}" for m in range(1, 13)] + [f"2025-{m:02d}" for m in range(1, 7)]
    for i, mo in enumerate(months):
        for sub, base_price in (("Hornsby", 1_500_000), ("Normanhurst", 1_200_000)):
            for band in ("<400", "400-600", "600-800"):
                n = 5 + i
                rows.append(
                    {
                        "month": mo,
                        "suburb": sub,
                        "area_band": band,
                        "n_sold": n,
                        "total_sale_value": n * base_price,
                    }
                )
    return pd.DataFrame(rows)


def test_slug_and_element_id() -> None:
    assert slug("Line Bar! Sale/Volume") == "line-bar-sale-volume"
    assert element_id_for("line-bar-sale-volume") == "obj:line-bar-sale-volume"


def test_needed_columns_reads_grain_and_measures() -> None:
    assert needed_columns(_SPEC) == {
        "month",
        "suburb",
        "area_band",
        "n_sold",
        "total_sale_value",
    }


def test_canonical_extract_uses_explicit_filter() -> None:
    sql = canonical_extract_sql(
        "SELECT ... growth query with no suburb name",
        grain=["month", "suburb", "area_band"],
        measure_source_cols={"n_sold", "total_sale_value"},
        where_override="property_type = 'house' AND suburb IN ('Hornsby', 'Normanhurst')",
    )
    assert "FROM marts.property_sales" in sql
    assert "GROUP BY month, suburb, area_band" in sql
    assert "sum(n_sold) AS n_sold" in sql
    assert "sum(total_sale_value) AS total_sale_value" in sql
    assert "WHERE property_type = 'house' AND suburb IN ('Hornsby', 'Normanhurst')" in sql


def test_canonical_extract_rejects_filter_with_nested_select() -> None:
    with pytest.raises(ValueError):
        canonical_extract_sql(
            "SELECT ... growth query with no suburb name",
            grain=["month", "suburb", "area_band"],
            measure_source_cols={"n_sold", "total_sale_value"},
            where_override="1=1 AND suburb IN (SELECT username FROM app.users)",
        )


def test_canonical_extract_rejects_filter_with_statement_separator() -> None:
    with pytest.raises(ValueError):
        canonical_extract_sql(
            "SELECT ... growth query with no suburb name",
            grain=["month", "suburb", "area_band"],
            measure_source_cols={"n_sold", "total_sale_value"},
            where_override="1=1; DROP TABLE marts.property_sales",
        )


def test_canonical_extract_carries_suburb_filter_when_no_override() -> None:
    sql = canonical_extract_sql(
        "SELECT * FROM marts.property_sales WHERE suburb IN ('Hornsby', 'Normanhurst') "
        "AND property_type = 'house'",
        grain=["month", "suburb", "area_band"],
        measure_source_cols={"n_sold", "total_sale_value"},
    )
    assert "suburb IN ('Hornsby', 'Normanhurst')" in sql
    assert "property_type = 'house'" in sql


def test_canonical_extract_preserves_original_filter_and_ands_override() -> None:
    # The golden's original filter (which the question captured) is never dropped
    # or replaced — the builder's filter field only ADDs a further predicate, so an
    # object narrows the same governed rows rather than re-scoping them.
    sql = canonical_extract_sql(
        "SELECT month, postcode FROM marts.property_rent "
        "WHERE postcode IN ('2077', '2076') GROUP BY month, postcode",
        grain=["month", "postcode"],
        measure_source_cols={"n_rented", "total_weekly_rent"},
        where_override="bedroom_band = '2'",
        dataset="nsw_rent",
    )
    assert "postcode IN ('2077', '2076')" in sql  # original preserved verbatim
    assert "bedroom_band = '2'" in sql  # additional filter ANDed on top
    # Two predicates are each parenthesised and joined with AND (neither replaced).
    assert "(postcode IN ('2077', '2076'))" in sql
    assert "(bedroom_band = '2')" in sql
    assert "WHERE (postcode" in sql and " AND (bedroom_band" in sql


def test_canonical_extract_preserves_non_equality_original_filter() -> None:
    # The old best-effort lift only carried IN/= predicates on the profile's carry
    # columns, so a range/date filter silently vanished on rewrite. The full
    # original WHERE is now preserved verbatim regardless of predicate shape.
    sql = canonical_extract_sql(
        "SELECT month FROM marts.property_sales "
        "WHERE suburb = 'Hornsby' AND month >= '2024-01-01' GROUP BY month",
        grain=["month"],
        measure_source_cols={"n_sold", "total_sale_value"},
    )
    assert "suburb = 'Hornsby'" in sql
    assert "month >= '2024-01-01'" in sql


def test_compare_object_lifts_to_combo_at_chart_grain() -> None:
    code = build_object_code(object_type="compare", spec=_SPEC)
    outcome = run_code(code, df=_frame(), frames={"extract": _frame()})
    assert outcome.error is None
    assert "dual_axis_chart" in outcome.skills_used

    obj = chart_object_from_spec(
        (outcome.report or {}).get("main_chart"),
        element_id=element_id_for("line-bar-sale-volume"),
        role="chart",
        height="md",
    )
    assert obj is not None
    d = obj.model_dump(exclude_none=True)
    assert d["type"] == "compare"
    assert d["element_id"] == "obj:line-bar-sale-volume"
    assert d["data"]["dimension"] == "area_band"
    assert d["data"]["measure"] == "sales_volume"
    assert d["data"]["line_measure"] == "avg_sale_price"
    assert d["data"]["group"] == "suburb"

    rows = d["data"]["rows"]
    # One row per (area_band, suburb) — 3 bands × 2 suburbs — so the combo renderer
    # (which does rows.find per category+group) plots every bar exactly once.
    assert len(rows) == 6
    keys = {(r["area_band"], r["suburb"]) for r in rows}
    assert len(keys) == 6
    # Price is value-weighted, so it separates the two suburbs.
    hornsby = next(r for r in rows if r["suburb"] == "Hornsby")
    normanhurst = next(r for r in rows if r["suburb"] == "Normanhurst")
    assert hornsby["avg_sale_price"] > normanhurst["avg_sale_price"]
    assert hornsby["sales_volume"] > 0


def _rent_frame() -> pd.DataFrame:
    """A rent mart extract: month × bedroom_band, additive n_rented/total_weekly_rent."""
    rows = []
    months = [f"2024-{m:02d}" for m in range(1, 13)] + [f"2025-{m:02d}" for m in range(1, 7)]
    for i, mo in enumerate(months):
        for band, base_rent in (("1", 450), ("2", 600), ("3", 780)):
            n = 20 + i
            rows.append(
                {
                    "month": mo,
                    "bedroom_band": band,
                    "n_rented": n,
                    "total_weekly_rent": n * base_rent,
                }
            )
    return pd.DataFrame(rows)


def test_name_from_instruction_slugs_salient_words() -> None:
    # Stopwords (by/as/the/only/…) dropped; first salient words kept, then slugged.
    assert (
        name_from_instruction(
            "average weekly rent by month as x axis, colour by bedroom band, houses only"
        )
        == "average-weekly-rent-month-x"
    )
    assert name_from_instruction("") == "object"


def test_rent_profile_canonical_extract_and_recompose() -> None:
    prof = profile_for("nsw_rent")
    assert prof.table == "marts.property_rent"
    sql = canonical_extract_sql(
        "SELECT * FROM marts.property_rent WHERE property_type = 'house'",
        grain=["month", "bedroom_band"],
        measure_source_cols={"n_rented", "total_weekly_rent"},
        dataset="nsw_rent",
    )
    assert "FROM marts.property_rent" in sql
    assert "sum(n_rented) AS n_rented" in sql
    assert "sum(total_weekly_rent) AS total_weekly_rent" in sql
    # Non-additive average recomposed as sum(value)/sum(count) — NOT avg-of-avgs.
    assert (
        "round((sum(total_weekly_rent) / NULLIF(sum(n_rented), 0))::numeric) AS avg_weekly_rent"
        in sql
    )
    # Carried filter: property_type equality lifted verbatim from the base SQL.
    assert "property_type = 'house'" in sql


def test_rent_trend_object_colours_by_bedroom_band() -> None:
    """The user's ask: a line chart of avg weekly rent by month, one series per
    bedroom band — built deterministically against the rent profile."""
    spec = {
        "group": "bedroom_band",
        "line_measure": {
            "label": "avg_weekly_rent",
            "num": "total_weekly_rent",
            "den": "n_rented",
        },
    }
    code = build_object_code(object_type="trend", spec=spec, dataset="nsw_rent")
    outcome = run_code(code, df=_rent_frame(), frames={"extract": _rent_frame()})
    assert outcome.error is None
    assert "trend_series" in outcome.skills_used
    assert "trend_chart" in outcome.skills_used


def test_breakdown_object_runs() -> None:
    spec = {
        "grain": ["suburb", "area_band"],
        "dimension": "area_band",
        "group": "suburb",
        "bar_measure": {"label": "sales_volume", "source": "n_sold", "agg": "sum"},
    }
    code = build_object_code(object_type="breakdown", spec=spec)
    outcome = run_code(code, df=_frame(), frames={"extract": _frame()})
    assert outcome.error is None
    assert "comparison_chart" in outcome.skills_used


def test_table_object_builds_and_lifts_to_valid_page_object() -> None:
    """s20 — the table builder emits skills.data_table + build_report(table=...),
    and the lift produces a `table` page object that passes the pages contract."""
    from agent.main import _lift_object
    from agent.pages import PageObject

    spec = {
        "grain": ["suburb", "area_band"],
        "dimension": "area_band",
        "group": "suburb",
        "bar_measure": {"label": "sales_volume", "source": "n_sold", "agg": "sum"},
        "variant": "ranked",
        "title": "Volume by band",
    }
    code = build_object_code(object_type="table", spec=spec)
    outcome = run_code(code, df=_frame(), frames={"extract": _frame()})
    assert outcome.error is None
    assert "data_table" in outcome.skills_used

    obj = _lift_object(
        outcome.report,
        element_id=element_id_for("volume-table"),
        object_type="table",
        sql="SELECT 1",
    )
    assert obj is not None
    assert obj["type"] == "table"
    assert obj["element_id"] == "obj:volume-table"
    data = obj["data"]
    assert data["variant"] == "ranked"
    assert data["bar_key"] == "sales_volume"
    assert data["sql"] == "SELECT 1"
    keys = [c["key"] for c in data["columns"]]
    assert keys == ["area_band", "suburb", "sales_volume"]
    # Ranked: rows sorted by the bar measure, descending.
    vols = [r["sales_volume"] for r in data["rows"]]
    assert vols == sorted(vols, reverse=True)
    # The lifted dict validates through the agent-side pages contract.
    PageObject(**obj)


# --- s28: augmented measure kinds (share / growth / latest) + composite x-axis ---


def _rent_mix_frame() -> pd.DataFrame:
    """A rent extract at postcode × property_type × bedroom_band × month."""
    rows = []
    months = [f"2024-{m:02d}" for m in range(1, 13)] + [f"2025-{m:02d}" for m in range(1, 7)]
    for mo in months:
        for pc in ("2077", "2076"):
            for pt in ("house", "unit"):
                for bb in ("1", "2", "3"):
                    n = 10 if pt == "unit" else 4
                    rows.append(
                        {
                            "month": mo,
                            "postcode": pc,
                            "property_type": pt,
                            "bedroom_band": bb,
                            "n_rented": n,
                            "total_weekly_rent": n * (400 + int(bb) * 120),
                        }
                    )
    return pd.DataFrame(rows)


def test_share_measure_over_composite_axis_sums_to_100_per_series() -> None:
    """The user's chart, deterministically: a Line+Bar whose bars are the % share
    of rentals within each postcode (a "mix"), over a composite bedroom_band ×
    property_type x-axis, with the value-weighted rent as the line. No LLM."""
    spec = {
        "grain": ["month", "postcode", "property_type", "bedroom_band"],
        "dimension": ["bedroom_band", "property_type"],  # composite axis
        "group": "postcode",
        "bar_measure": {"label": "share", "source": "n_rented", "how": "share"},
        "line_measure": {"label": "avg_rent", "num": "total_weekly_rent", "den": "n_rented"},
        "months": 12,
    }
    code = build_object_code(object_type="compare", spec=spec, dataset="nsw_rent")
    outcome = run_code(code, df=_rent_mix_frame(), frames={"extract": _rent_mix_frame()})
    assert outcome.error is None
    assert "dual_axis_chart" in outcome.skills_used

    obj = chart_object_from_spec(
        (outcome.report or {}).get("main_chart"),
        element_id=element_id_for("rent-mix"),
        role="chart",
        height="md",
    )
    assert obj is not None
    d = obj.model_dump(exclude_none=True)
    assert d["type"] == "compare"
    assert d["data"]["dimension"] == "_x"  # the synthesized composite axis
    assert d["data"]["measure"] == "share"
    assert d["data"]["line_measure"] == "avg_rent"
    assert d["data"]["group"] == "postcode"

    rows = d["data"]["rows"]
    assert rows and all("-" in str(r["_x"]) for r in rows)  # "1-house", …
    per_postcode: dict[str, float] = {}
    for r in rows:
        per_postcode[r["postcode"]] = per_postcode.get(r["postcode"], 0.0) + float(r["share"])
    assert per_postcode and all(abs(s - 100.0) < 0.5 for s in per_postcode.values())
    assert all(0.0 <= float(r["share"]) <= 100.0 for r in rows)


def test_growth_measure_kind_runs_and_is_positive() -> None:
    """`how: growth` augments a base metric into a first-vs-last % change per key."""
    spec = {
        "grain": ["month", "bedroom_band"],
        "dimension": "bedroom_band",
        "bar_measure": {"label": "rent_growth", "source": "total_weekly_rent", "how": "growth"},
        "months": 18,
    }
    code = build_object_code(object_type="breakdown", spec=spec, dataset="nsw_rent")
    outcome = run_code(code, df=_rent_frame(), frames={"extract": _rent_frame()})
    assert outcome.error is None
    values = ((outcome.report or {}).get("main_chart") or {}).get("data", {}).get("values", [])
    assert values and all("rent_growth" in r for r in values)
    # The fixture's counts grow 20→37, so every band's growth must be positive —
    # this locks the first-vs-last ordering, not just the column's presence.
    assert all(float(r["rent_growth"]) > 0 for r in values)


def test_latest_measure_kind_runs() -> None:
    """`how: latest` takes the most recent month's value per key."""
    spec = {
        "grain": ["month", "bedroom_band"],
        "dimension": "bedroom_band",
        "bar_measure": {"label": "current", "source": "n_rented", "how": "latest"},
        "months": 12,
    }
    code = build_object_code(object_type="breakdown", spec=spec, dataset="nsw_rent")
    outcome = run_code(code, df=_rent_frame(), frames={"extract": _rent_frame()})
    assert outcome.error is None
    values = ((outcome.report or {}).get("main_chart") or {}).get("data", {}).get("values", [])
    assert values and all("current" in r for r in values)


def _growing_mix_frame() -> pd.DataFrame:
    """Rentals at postcode × property_type × bedroom_band × month whose per-month
    totals per band grow 28 → 96 while the sub-slice rows differ (house 4+i vs
    unit 10+i), so any single row is a wrong stand-in for its month's total."""
    rows = []
    months = [f"2024-{m:02d}" for m in range(1, 13)] + [f"2025-{m:02d}" for m in range(1, 7)]
    for i, mo in enumerate(months):
        for pc in ("2077", "2076"):
            for pt in ("house", "unit"):
                for bb in ("1", "2", "3"):
                    n = (10 if pt == "unit" else 4) + i
                    rows.append(
                        {
                            "month": mo,
                            "postcode": pc,
                            "property_type": pt,
                            "bedroom_band": bb,
                            "n_rented": n,
                            "total_weekly_rent": n * (400 + int(bb) * 120),
                        }
                    )
    return pd.DataFrame(rows)


def _breakdown_values(spec: dict[str, Any], df: pd.DataFrame) -> list[dict[str, Any]]:
    code = build_object_code(object_type="breakdown", spec=spec, dataset="nsw_rent")
    outcome = run_code(code, df=df, frames={"extract": df})
    assert outcome.error is None
    values = ((outcome.report or {}).get("main_chart") or {}).get("data", {}).get("values", [])
    return list(values)


def test_growth_and_latest_use_per_month_totals_over_wider_grain() -> None:
    """growth/latest collapse the source to per-month totals before taking
    first/last, so a grain wider than the chart keys (postcode × property_type
    here) contributes its month's total, not one sub-slice row.

    Every band's per-month total grows 28 → 96, so true growth is exactly
    (96-28)/28 = 242.9% and the latest value 96 — no first/last over raw
    sub-slice rows (4+i / 10+i) can produce either number.
    """
    growth = _breakdown_values(
        {
            "grain": ["month", "postcode", "property_type", "bedroom_band"],
            "dimension": "bedroom_band",
            "bar_measure": {"label": "rent_growth", "source": "n_rented", "how": "growth"},
            "months": 18,
        },
        _growing_mix_frame(),
    )
    assert growth and all(float(r["rent_growth"]) == 242.9 for r in growth)

    latest = _breakdown_values(
        {
            "grain": ["month", "postcode", "property_type", "bedroom_band"],
            "dimension": "bedroom_band",
            "bar_measure": {"label": "current", "source": "n_rented", "how": "latest"},
            "months": 18,
        },
        _growing_mix_frame(),
    )
    assert latest and all(float(r["current"]) == 96.0 for r in latest)


def test_yield_extract_selects_non_profile_measure_sources() -> None:
    """The yield profile's legs are the rent pair, but the mart also carries the
    additive sales legs — a spec sourcing them gets them summed into the extract
    (the previously unused ``measure_source_cols``)."""
    sql = canonical_extract_sql(
        "SELECT * FROM marts.property_yield WHERE property_type = 'house'",
        grain=["month", "postcode"],
        measure_source_cols={"n_sold", "total_sale_value"},
        dataset="nsw_yield",
    )
    assert "FROM marts.property_yield" in sql
    assert "sum(n_rented) AS n_rented" in sql
    assert "sum(n_sold) AS n_sold" in sql
    assert "sum(total_sale_value) AS total_sale_value" in sql


def _yield_frame() -> pd.DataFrame:
    """A yield mart extract at postcode × month with all four additive legs."""
    rows = []
    months = [f"2024-{m:02d}" for m in range(1, 13)]
    for i, mo in enumerate(months):
        for pc in ("2077", "2076"):
            n = 5 + i
            rows.append(
                {
                    "month": mo,
                    "postcode": pc,
                    "n_sold": n,
                    "total_sale_value": n * 1_000_000,
                    "n_rented": n * 3,
                    "total_weekly_rent": n * 3 * 550,
                }
            )
    return pd.DataFrame(rows)


def test_yield_breakdown_over_sales_leg_survives_dedup() -> None:
    """A measure over ``n_sold`` on nsw_yield builds: the window dedup sums the
    union of the profile legs and the measure sources, not just the rent pair."""
    spec = {
        "grain": ["month", "postcode"],
        "dimension": "postcode",
        "bar_measure": {"label": "sales", "source": "n_sold", "agg": "sum"},
    }
    code = build_object_code(object_type="breakdown", spec=spec, dataset="nsw_yield")
    outcome = run_code(code, df=_yield_frame(), frames={"extract": _yield_frame()})
    assert outcome.error is None
    values = ((outcome.report or {}).get("main_chart") or {}).get("data", {}).get("values", [])
    assert values and all("sales" in r for r in values)
    assert all(float(r["sales"]) > 0 for r in values)


def test_label_with_apostrophe_emits_runnable_code() -> None:
    """Labels are json-escaped into the snippet, so an apostrophe can't break it."""
    spec = {
        "grain": ["month", "bedroom_band"],
        "dimension": "bedroom_band",
        "bar_measure": {"label": "what's rented", "source": "n_rented", "how": "share"},
    }
    code = build_object_code(object_type="breakdown", spec=spec, dataset="nsw_rent")
    outcome = run_code(code, df=_rent_frame(), frames={"extract": _rent_frame()})
    assert outcome.error is None
    values = ((outcome.report or {}).get("main_chart") or {}).get("data", {}).get("values", [])
    assert values and all("what's rented" in r for r in values)


def test_extract_grain_extends_bar_family_only() -> None:
    """Bar-family extracts append the dimension/group columns their snippet
    groups by; trend/kpi keep the typed grain so their per-month numbers can't
    shift to a finer grain."""
    spec = {
        "grain": ["month"],
        "dimension": ["bedroom_band", "property_type"],
        "group": "postcode",
    }
    for object_type in ("compare", "breakdown", "table"):
        assert extract_grain(spec, object_type=object_type, dataset="nsw_rent") == [
            "month",
            "bedroom_band",
            "property_type",
            "postcode",
        ]
    for object_type in ("trend", "kpi"):
        assert extract_grain(spec, object_type=object_type, dataset="nsw_rent") == ["month"]


def test_canonical_extract_rejects_non_identifier_grain() -> None:
    with pytest.raises(ValueError, match="identifier"):
        canonical_extract_sql(
            "SELECT 1",
            grain=["month", "(SELECT username FROM app.users LIMIT 1)"],
            measure_source_cols=set(),
        )


def test_canonical_extract_rejects_non_identifier_measure_source() -> None:
    with pytest.raises(ValueError, match="identifier"):
        canonical_extract_sql(
            "SELECT 1",
            grain=["month", "postcode"],
            measure_source_cols={"n_sold) AS x FROM app.users --"},
            dataset="nsw_yield",
        )


def test_canonical_extract_rejects_non_additive_measure_source() -> None:
    """gross_yield_pct is a real yield-mart column, but summing a ratio across
    grain rows is silently wrong — the extract refuses instead."""
    with pytest.raises(ValueError, match="not additive"):
        canonical_extract_sql(
            "SELECT 1",
            grain=["month", "postcode"],
            measure_source_cols={"gross_yield_pct"},
            dataset="nsw_yield",
        )


def test_build_object_code_rejects_summed_non_additive_source() -> None:
    spec = {
        "grain": ["month", "postcode"],
        "dimension": "postcode",
        "bar_measure": {"label": "yield", "source": "gross_yield_pct", "agg": "sum"},
    }
    with pytest.raises(ValueError, match="not additive"):
        build_object_code(object_type="breakdown", spec=spec, dataset="nsw_yield")


def test_build_object_code_rejects_share_of_non_additive_source() -> None:
    spec = {
        "grain": ["month", "bedroom_band"],
        "dimension": "bedroom_band",
        "bar_measure": {"label": "mix", "source": "avg_weekly_rent", "how": "share"},
    }
    with pytest.raises(ValueError, match="not additive"):
        build_object_code(object_type="breakdown", spec=spec, dataset="nsw_rent")


def test_build_object_code_rejects_mean_of_non_additive_source() -> None:
    """The window dedup sums a mean measure's source before the mean is taken,
    so averaging a stored average charts silently wrong numbers — refuse it."""
    spec = {
        "grain": ["month", "bedroom_band"],
        "dimension": "bedroom_band",
        "bar_measure": {"label": "rent", "source": "avg_weekly_rent", "agg": "mean"},
    }
    with pytest.raises(ValueError, match="not additive"):
        build_object_code(object_type="breakdown", spec=spec, dataset="nsw_rent")


def test_build_object_code_rejects_non_identifier_column() -> None:
    spec = {
        "grain": ["month"],
        "dimension": "postcode'; import os",
        "bar_measure": {"label": "n", "source": "n_rented", "agg": "sum"},
    }
    with pytest.raises(ValueError, match="identifier"):
        build_object_code(object_type="breakdown", spec=spec, dataset="nsw_rent")


def test_trend_column_mode_over_ratio_still_builds() -> None:
    """A trend plotting a bucket-level average directly (line_mode 'column') is
    legitimate — the extract recomposes it per month — so the additive guard
    must not reject it."""
    spec = {"line_measure": {"label": "avg_weekly_rent", "source": "avg_weekly_rent"}}
    code = build_object_code(object_type="trend", spec=spec, dataset="nsw_rent")
    assert 'value_col="avg_weekly_rent"' in code


def test_table_supports_composite_dimension() -> None:
    """A list `dimension` on a table becomes the synthesized `_x` axis column
    (labelled with the joined dimension names), exactly as compare/breakdown."""
    from agent.main import _lift_object
    from agent.pages import PageObject

    spec = {
        "grain": ["month", "postcode", "property_type", "bedroom_band"],
        "dimension": ["bedroom_band", "property_type"],
        "bar_measure": {"label": "volume", "source": "n_rented", "agg": "sum"},
        "variant": "ranked",
    }
    code = build_object_code(object_type="table", spec=spec, dataset="nsw_rent")
    outcome = run_code(code, df=_rent_mix_frame(), frames={"extract": _rent_mix_frame()})
    assert outcome.error is None
    assert "data_table" in outcome.skills_used

    obj = _lift_object(
        outcome.report,
        element_id=element_id_for("mix-table"),
        object_type="table",
        sql="SELECT 1",
    )
    assert obj is not None
    data = obj["data"]
    assert [c["key"] for c in data["columns"]] == ["_x", "volume"]
    labels = {c["key"]: c["label"] for c in data["columns"]}
    assert labels["_x"] == "bedroom_band · property_type"
    assert data["rows"] and all("-" in str(r["_x"]) for r in data["rows"])
    PageObject(**obj)


def test_composite_x_columns_land_in_the_regenerated_extract() -> None:
    """s28 build flow: when the x-axis is a composite of grain columns, both columns
    are in needed_columns AND the regenerated extract's GROUP BY — so 'check whether
    the SQL extract has the columns, else rewrite and rerun it' lands them at the
    right grain before the sandbox builds the object."""
    spec = {
        "grain": ["month", "postcode", "property_type", "bedroom_band"],
        "dimension": ["bedroom_band", "property_type"],  # composite x from the grain
        "group": "postcode",
        "bar_measure": {"label": "n", "source": "n_rented", "agg": "sum"},
    }
    need = needed_columns(spec)
    assert {"bedroom_band", "property_type", "postcode", "n_rented"} <= need

    sql = canonical_extract_sql(
        "SELECT * FROM marts.property_rent WHERE postcode IN ('2077', '2076')",
        grain=spec["grain"],
        measure_source_cols=need,
        dataset="nsw_rent",
    )
    assert "FROM marts.property_rent" in sql
    assert "GROUP BY month, postcode, property_type, bedroom_band" in sql
    # The composite x uses a dash join in the generated sandbox code (concat(x1,'-',x2)).
    code = build_object_code(object_type="breakdown", spec=spec, dataset="nsw_rent")
    assert "+ '-' +" in code
