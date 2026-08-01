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


def test_growth_derive_is_period_over_period() -> None:
    """A `growth` derive is a period-over-period % change — the base over the
    recent window vs the base over the prior window. Old goldens' `how: growth`
    maps onto the new derive (backwards compatible).

    total_weekly_rent per band-month is (20+i)*base_rent. Over a 9-month window the
    recent 9 months sum to base_rent*297 and the prior 9 to base_rent*216, so every
    band's growth is exactly (297-216)/216 = 37.5% — not a first-vs-last month read.
    """
    spec = {
        "grain": ["month", "bedroom_band"],
        "dimension": "bedroom_band",
        "bar_measure": {"label": "rent_growth", "source": "total_weekly_rent", "how": "growth"},
        "months": 9,
    }
    code = build_object_code(object_type="breakdown", spec=spec, dataset="nsw_rent")
    outcome = run_code(code, df=_rent_frame(), frames={"extract": _rent_frame()})
    assert outcome.error is None
    values = ((outcome.report or {}).get("main_chart") or {}).get("data", {}).get("values", [])
    assert values and all(float(r["rent_growth"]) == 37.5 for r in values)


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
    """growth/latest collapse the source to per-month totals before comparing, so
    a grain wider than the chart keys (postcode × property_type here) contributes
    its month's total, not one sub-slice row.

    Every band's per-month total grows 28 → 96. Growth is period over period: over a
    9-month window the recent 9 months total 720 and the prior 9 total 396, so
    growth is (720-396)/396 = 81.8%; latest is the last month's 96.
    """
    growth = _breakdown_values(
        {
            "grain": ["month", "postcode", "property_type", "bedroom_band"],
            "dimension": "bedroom_band",
            "bar_measure": {"label": "rent_growth", "source": "n_rented", "how": "growth"},
            "months": 9,
        },
        _growing_mix_frame(),
    )
    assert growth and all(float(r["rent_growth"]) == 81.8 for r in growth)

    latest = _breakdown_values(
        {
            "grain": ["month", "postcode", "property_type", "bedroom_band"],
            "dimension": "bedroom_band",
            "bar_measure": {"label": "current", "source": "n_rented", "how": "latest"},
            "months": 9,
        },
        _growing_mix_frame(),
    )
    assert latest and all(float(r["current"]) == 96.0 for r in latest)


def _band_derive(derive: str, source: str, months: int = 9) -> dict[str, float]:
    """One value per bedroom_band for a derive over ``_rent_frame`` (n_rented =
    20+i over 18 months; the 9-month window is months i=9..17, n 29..37)."""
    rows = _breakdown_values(
        {
            "grain": ["month", "bedroom_band"],
            "dimension": "bedroom_band",
            "bar_measure": {"label": "v", "source": source, "derive": derive},
            "months": months,
        },
        _rent_frame(),
    )
    return {r["bedroom_band"]: float(r["v"]) for r in rows}


def test_rolling_index_cumulative_yoy_derives_over_window() -> None:
    """The s31 time derives reduce a base metric to one value per key over the
    window: rolling = window mean, index = latest ÷ first × 100, cumulative =
    window total, yoy = latest vs 12 months prior."""
    # rolling = mean of the monthly n_rented over the window = mean(29..37) = 33
    assert all(v == 33.0 for v in _band_derive("rolling", "n_rented").values())
    # index = latest / first * 100 = 37 / 29 * 100 = 127.6
    assert all(v == 127.6 for v in _band_derive("index", "n_rented").values())
    # cumulative = window total = sum(29..37) = 297
    assert all(v == 297.0 for v in _band_derive("cumulative", "n_rented").values())
    # yoy = latest vs the month 12 prior = (37 - 25) / 25 * 100 = 48.0
    assert all(v == 48.0 for v in _band_derive("yoy", "n_rented").values())


def test_rank_derive_orders_within_series() -> None:
    """rank is a dense rank of the window value (1 = highest). total_weekly_rent
    scales with base_rent (450 < 600 < 780), so band 3 ranks 1 and band 1 ranks 3."""
    rank = _band_derive("rank", "total_weekly_rent")
    assert rank == {"1": 3.0, "2": 2.0, "3": 1.0}


def test_derive_guards_reject_invalid_combos() -> None:
    """% of total / cumulative need an additive sum base; time derives need month
    in the grain. Both are rejected at codegen rather than computed wrongly."""
    with pytest.raises(ValueError):  # share on a non-sum (mean) base
        build_object_code(
            object_type="breakdown",
            dataset="nsw_rent",
            spec={
                "grain": ["month", "bedroom_band"],
                "dimension": "bedroom_band",
                "bar_measure": {
                    "label": "x",
                    "agg": "mean",
                    "source": "n_rented",
                    "derive": "share",
                },
            },
        )
    with pytest.raises(ValueError):  # growth with no month in the grain
        build_object_code(
            object_type="breakdown",
            dataset="nsw_rent",
            spec={
                "grain": ["bedroom_band"],
                "dimension": "bedroom_band",
                "bar_measure": {"label": "x", "source": "n_rented", "derive": "growth"},
            },
        )


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
    """Every charting type appends the columns its snippet groups by — a trend
    now has a real x axis and aggregates to (x, group) itself, so its extract
    must carry both. Only kpi (which has no axis) keeps the typed grain plus a
    defensive ``group``."""
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
    # trend: typed grain + its x axis + the series column.
    assert extract_grain(spec, object_type="trend", dataset="nsw_rent") == [
        "month",
        "bedroom_band",
        "postcode",
    ]
    # A trend with no dimension keeps the TIME axis rather than falling back to
    # the profile's first categorical.
    assert extract_grain({"grain": ["month"]}, object_type="trend", dataset="nsw_rent") == ["month"]
    assert extract_grain(spec, object_type="kpi", dataset="nsw_rent") == ["month", "postcode"]


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


def test_trend_over_a_bare_ratio_column_is_rejected() -> None:
    """A trend now AGGREGATES the extract to its own (x, group) grain, so a bare
    ratio column can no longer be plotted per row: collapsing it would be an
    average-of-averages. The guard says so instead of drawing a wrong line —
    matching what the builder UI already refuses to submit."""
    spec = {"line_measure": {"label": "avg_weekly_rent", "source": "avg_weekly_rent"}}
    with pytest.raises(ValueError, match="not additive"):
        build_object_code(object_type="trend", spec=spec, dataset="nsw_rent")

    # The correct expression of the same intent — recompose from the legs.
    wavg = {
        "line_measure": {"label": "avg_weekly_rent", "num": "total_weekly_rent", "den": "n_rented"}
    }
    code = build_object_code(object_type="trend", spec=wavg, dataset="nsw_rent")
    assert "_num=(\"total_weekly_rent\", 'sum'), _den=(\"n_rented\", 'sum')" in code


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


# ---------------------------------------------------------------------------
# Pivot table (s34) — rows x a pivoted dimension, metrics in the cells.
# ---------------------------------------------------------------------------
def _rent_pivot_spec() -> dict:
    """The curator's motivating case: bedrooms/type down the side, postcode
    across the top, bond volume + average weekly rent in each cell."""
    return {
        "dimension": ["bedroom_band", "property_type"],
        "pivot_column": "postcode",
        "pivot_measures": [
            {"label": "bonds", "source": "n_rented", "agg": "sum", "months": 12},
            {
                "label": "avg_weekly_rent",
                "num": "total_weekly_rent",
                "den": "n_rented",
                "months": 12,
            },
        ],
        "months": 12,
        "title": "rent by postcode",
    }


def test_pivot_extract_grain_carries_rows_and_the_pivoted_column() -> None:
    """The extract must be grouped finely enough to fill every cell: a pivot
    grouped coarser than rows x column would average cells together."""
    spec = _rent_pivot_spec()
    grain = extract_grain(spec, object_type="pivot", dataset="nsw_rent")
    assert {"bedroom_band", "property_type", "postcode"} <= set(grain)
    assert "month" in grain  # the 12-month window reads it
    # And the columns the measures read are pulled into the regenerated extract.
    assert {"n_rented", "total_weekly_rent"} <= needed_columns(spec)


def test_pivot_recomposes_a_ratio_rather_than_averaging_averages() -> None:
    spec = _rent_pivot_spec()
    code = build_object_code(object_type="pivot", spec=spec, dataset="nsw_rent")
    # The ratio is built from its two additive legs, never summed as avg_*.
    assert "_num=(\"total_weekly_rent\", 'sum'), _den=(\"n_rented\", 'sum')" in code
    assert "avg_weekly_rent" in code
    # One column per (postcode, metric), grouped so a postcode's metrics adjoin.
    assert "pivot_table(index=_rows, columns=_col, values=_vals" in code
    assert "for mv in _vals for cv in _col_vals" in code
    assert code.rstrip().endswith("table=table)")


def test_pivot_rejects_a_column_that_is_also_a_row() -> None:
    spec = _rent_pivot_spec()
    spec["pivot_column"] = "bedroom_band"
    with pytest.raises(ValueError, match="also a row column"):
        build_object_code(object_type="pivot", spec=spec, dataset="nsw_rent")


def test_pivot_needs_a_column_and_at_least_one_metric() -> None:
    spec = _rent_pivot_spec()
    spec["pivot_column"] = ""
    with pytest.raises(ValueError, match="pivot_column"):
        build_object_code(object_type="pivot", spec=spec, dataset="nsw_rent")
    spec = _rent_pivot_spec()
    spec["pivot_measures"] = []
    with pytest.raises(ValueError, match="at least one metric"):
        build_object_code(object_type="pivot", spec=spec, dataset="nsw_rent")


def _rent_pivot_frame() -> pd.DataFrame:
    """A rent extract with the three cuts a pivot needs: postcode (pivoted across),
    bedroom_band + property_type (down the side), and month for the window."""
    rows = []
    months = [f"2025-{m:02d}" for m in range(1, 13)]
    for i, mo in enumerate(months):
        for postcode in ("2076", "2077"):
            for band, base in (("1", 450), ("2", 600)):
                for ptype in ("house", "unit"):
                    n = 10 + i
                    rent = (
                        base + (50 if postcode == "2077" else 0) + (30 if ptype == "house" else 0)
                    )
                    rows.append(
                        {
                            "month": mo,
                            "postcode": postcode,
                            "bedroom_band": band,
                            "property_type": ptype,
                            "n_rented": n,
                            "total_weekly_rent": n * rent,
                        }
                    )
    return pd.DataFrame(rows)


def test_pivot_object_runs_and_lifts_to_a_valid_table_page_object() -> None:
    """End to end in the sandbox: the generated pandas produces one row per
    bedroom_band x property_type and a column per (postcode, metric), which is
    the whole point — comparing postcodes reads ACROSS a row."""
    spec = _rent_pivot_spec()
    code = build_object_code(object_type="pivot", spec=spec, dataset="nsw_rent")
    frame = _rent_pivot_frame()
    outcome = run_code(code, df=frame, frames={"extract": frame})
    assert outcome.error is None, outcome.error
    assert "data_table" in outcome.skills_used

    from agent.main import _lift_object
    from agent.pages import PageObject

    # Lifted with the BUILD type the router passes ("pivot"), not "table" — the
    # cross-tab still has to come out as a plain table page object.
    obj = _lift_object(
        outcome.report,
        element_id=element_id_for("rent-pivot"),
        object_type="pivot",
        sql="SELECT 1",
    )
    assert obj["type"] == "table"
    assert obj is not None
    data = obj["data"]
    keys = [c["key"] for c in data["columns"]]
    assert keys[:2] == ["bedroom_band", "property_type"]
    # Columns are grouped by METRIC, its pivoted values adjacent within each.
    # METRIC-major: each metric's postcodes adjoin, so the two numbers you want
    # to compare sit next to each other.
    assert keys[2:] == [
        "bonds · 2076",
        "bonds · 2077",
        "avg_weekly_rent · 2076",
        "avg_weekly_rent · 2077",
    ]
    # 2 bands x 2 property types = 4 rows.
    assert len(data["rows"]) == 4
    row = next(
        r for r in data["rows"] if r["bedroom_band"] == "2" and r["property_type"] == "house"
    )
    # 2077 is the dearer postcode in the fixture (+50) — the pivot makes that a
    # single left-to-right comparison, and the ratio is recomposed not averaged.
    assert row["avg_weekly_rent · 2077"] > row["avg_weekly_rent · 2076"]
    assert row["avg_weekly_rent · 2076"] == 630  # 600 base + 30 house
    assert row["avg_weekly_rent · 2077"] == 680  # + 50 postcode premium
    PageObject(**obj)


def _rent_frame_finer_than_the_chart() -> pd.DataFrame:
    """A rent extract at (month, postcode, bedroom_band) — FINER than a trend of
    avg rent by month. Two postcodes per month per band, with different volumes,
    so a per-row plot and a correctly-aggregated one cannot coincide."""
    rows = []
    for i, mo in enumerate([f"2025-{m:02d}" for m in range(1, 13)]):
        for postcode, n, rent in (("2076", 10, 500), ("2077", 90, 1000)):
            rows.append(
                {
                    "month": mo,
                    "postcode": postcode,
                    "bedroom_band": "2",
                    "n_rented": n,
                    "total_weekly_rent": n * (rent + i),
                }
            )
    return pd.DataFrame(rows)


def test_trend_aggregates_the_extract_to_its_own_grain() -> None:
    """The regression this fixes: trend_series emits one point per ROW, so an
    extract finer than the chart drew several points on the same month and
    divided per row. The snippet now sums the legs to (month) first, so each
    month has ONE point and it is a volume-weighted ratio of sums."""
    spec = {
        "grain": ["month", "postcode", "bedroom_band"],
        "line_measure": {
            "label": "avg_weekly_rent",
            "num": "total_weekly_rent",
            "den": "n_rented",
        },
    }
    code = build_object_code(object_type="trend", spec=spec, dataset="nsw_rent")
    frame = _rent_frame_finer_than_the_chart()
    outcome = run_code(code, df=frame, frames={"extract": frame})
    assert outcome.error is None, outcome.error

    points = outcome.report["main_chart"]["data"]["values"]
    actual = [p for p in points if p["layer"] == "actual"]
    # One point per month, not one per (month, postcode).
    assert len(actual) == 12, actual
    assert len({p["month"] for p in actual}) == 12
    # Volume weighted: (10*500 + 90*1000) / 100 = 950 for the first month — NOT
    # the unweighted mean of the two bucket averages (750).
    first = min(actual, key=lambda p: p["month"])
    assert first["value"] == 950.0


def test_trend_can_plot_a_non_time_x_axis() -> None:
    """A line chart over a category (the curator picks the x axis): the values
    are aggregated to that axis, the x stays verbatim rather than being coerced
    into a date, and the axis is declared ordinal rather than temporal."""
    spec = {
        "grain": ["month", "postcode", "bedroom_band"],
        "dimension": ["bedroom_band"],
        "line_measure": {
            "label": "avg_weekly_rent",
            "num": "total_weekly_rent",
            "den": "n_rented",
        },
    }
    code = build_object_code(object_type="trend", spec=spec, dataset="nsw_rent")
    assert "date_axis=False" in code
    assert "x_type='ordinal'" in code
    frame = _rent_pivot_frame()
    outcome = run_code(code, df=frame, frames={"extract": frame})
    assert outcome.error is None, outcome.error
    chart = outcome.report["main_chart"]
    assert chart["encoding"]["x"]["type"] == "ordinal"
    xs = sorted({p["month"] for p in chart["data"]["values"]})
    assert xs == ["1", "2"]  # the fixture's bedroom bands, not YYYY-MM-01 dates


def test_pivot_difference_column_states_the_gap() -> None:
    """The point of pivoting two postcodes: the table can state the gap between
    them per metric, rather than leaving the reader to subtract two columns. The
    Δ sits directly after its own metric's block."""
    spec = _rent_pivot_spec()
    spec["pivot_compare"] = "diff"
    code = build_object_code(object_type="pivot", spec=spec, dataset="nsw_rent")
    frame = _rent_pivot_frame()
    outcome = run_code(code, df=frame, frames={"extract": frame})
    assert outcome.error is None, outcome.error

    data = outcome.report["table"]
    keys = [c["key"] for c in data["columns"]]
    assert keys[2:] == [
        "bonds · 2076",
        "bonds · 2077",
        "bonds · Δ",
        "avg_weekly_rent · 2076",
        "avg_weekly_rent · 2077",
        "avg_weekly_rent · Δ",
    ]
    row = next(
        r for r in data["rows"] if r["bedroom_band"] == "2" and r["property_type"] == "house"
    )
    # 680 - 630 = 50, the fixture's postcode premium, computed not eyeballed.
    assert row["avg_weekly_rent · Δ"] == 50
    assert row["bonds · Δ"] == 0  # same volumes in both postcodes


def test_pivot_percentage_difference() -> None:
    spec = _rent_pivot_spec()
    spec["pivot_compare"] = "pct_diff"
    code = build_object_code(object_type="pivot", spec=spec, dataset="nsw_rent")
    frame = _rent_pivot_frame()
    outcome = run_code(code, df=frame, frames={"extract": frame})
    assert outcome.error is None, outcome.error
    data = outcome.report["table"]
    assert "avg_weekly_rent · Δ%" in [c["key"] for c in data["columns"]]
    row = next(
        r for r in data["rows"] if r["bedroom_band"] == "2" and r["property_type"] == "house"
    )
    assert row["avg_weekly_rent · Δ%"] == round(50 * 100 / 630, 2)


def test_pivot_difference_needs_exactly_two_pivoted_values() -> None:
    """With three postcodes there is no single "the difference", so the columns
    are omitted rather than silently comparing an arbitrary pair."""
    spec = _rent_pivot_spec()
    spec["pivot_compare"] = "diff"
    code = build_object_code(object_type="pivot", spec=spec, dataset="nsw_rent")
    frame = _rent_pivot_frame()
    extra = frame.copy()
    extra["postcode"] = "2079"
    frame = pd.concat([frame, extra], ignore_index=True)
    outcome = run_code(code, df=frame, frames={"extract": frame})
    assert outcome.error is None, outcome.error
    keys = [c["key"] for c in outcome.report["table"]["columns"]]
    assert not any(k.endswith("· Δ") for k in keys)
    assert "bonds · 2079" in keys


def test_pivot_difference_columns_are_coloured_by_sign() -> None:
    """A signed gap is the one number whose DIRECTION is the point, so the Δ
    columns always carry the delta tone (green up / red down) while the plain
    value columns stay neutral."""
    spec = _rent_pivot_spec()
    spec["pivot_compare"] = "diff"
    code = build_object_code(object_type="pivot", spec=spec, dataset="nsw_rent")
    frame = _rent_pivot_frame()
    outcome = run_code(code, df=frame, frames={"extract": frame})
    assert outcome.error is None, outcome.error
    tones = {c["key"]: c.get("tone") for c in outcome.report["table"]["columns"]}
    assert tones["avg_weekly_rent · Δ"] == "delta"
    assert tones["bonds · Δ"] == "delta"
    assert tones["avg_weekly_rent · 2076"] is None


def test_pivot_color_by_sign_extends_to_every_metric_column() -> None:
    spec = _rent_pivot_spec()
    spec["color_by_sign"] = True
    code = build_object_code(object_type="pivot", spec=spec, dataset="nsw_rent")
    frame = _rent_pivot_frame()
    outcome = run_code(code, df=frame, frames={"extract": frame})
    assert outcome.error is None, outcome.error
    cols = outcome.report["table"]["columns"]
    metric_cols = [c for c in cols if c["key"] not in ("bedroom_band", "property_type")]
    assert metric_cols and all(c.get("tone") == "delta" for c in metric_cols)
    # Row labels stay uncoloured — only numbers have a sign.
    assert all(c.get("tone") is None for c in cols if c["key"] == "bedroom_band")


def _trend_points(spec: dict, frame: pd.DataFrame) -> list[dict]:
    code = build_object_code(object_type="trend", spec=spec, dataset="nsw_rent")
    outcome = run_code(code, df=frame, frames={"extract": frame})
    assert outcome.error is None, outcome.error
    return outcome.report["main_chart"]["data"]["values"]


def test_trend_smoothing_layers_are_the_curators_choice() -> None:
    """The faint actual line under the rolling average, and the window that
    sizes it, were both hardcoded. Each is now a spec option."""
    base = {
        "grain": ["month", "postcode", "bedroom_band"],
        "line_measure": {
            "label": "avg_weekly_rent",
            "num": "total_weekly_rent",
            "den": "n_rented",
        },
    }
    frame = _rent_frame_finer_than_the_chart()

    layers = lambda pts: {p["layer"] for p in pts}  # noqa: E731
    # Default: actual + a 6-month rolling overlay, as before.
    assert layers(_trend_points(base, frame)) == {"actual", "6-mo avg"}
    # Actual off: only the smoothed line remains.
    assert layers(_trend_points({**base, "show_actual": False}, frame)) == {"6-mo avg"}
    # A different window renames the layer, so the chart says what it is.
    assert layers(_trend_points({**base, "rolling_window": 3}, frame)) == {"actual", "3-mo avg"}
    # No smoothing at all: just the actual line.
    assert layers(_trend_points({**base, "rolling_window": 0}, frame)) == {"actual"}
    # Turning both off would leave an empty chart, so actual survives.
    both_off = {**base, "rolling_window": 0, "show_actual": False}
    assert layers(_trend_points(both_off, frame)) == {"actual"}


def test_multi_attribute_sort_orders_by_priority() -> None:
    """Databricks-style ordering: the sort list IS the priority. Sort by
    property_type ascending, then break ties by bonds descending."""
    spec = _rent_pivot_spec()
    spec["sort"] = [
        {"col": "property_type", "dir": "asc"},
        {"col": "bonds · 2077", "dir": "desc"},
    ]
    code = build_object_code(object_type="pivot", spec=spec, dataset="nsw_rent")
    frame = _rent_pivot_frame()
    outcome = run_code(code, df=frame, frames={"extract": frame})
    assert outcome.error is None, outcome.error
    rows = outcome.report["table"]["rows"]
    # P1: every house before every unit.
    types = [r["property_type"] for r in rows]
    assert types == sorted(types)
    # P2: within a property type, bonds descending.
    houses = [r["bonds · 2077"] for r in rows if r["property_type"] == "house"]
    assert houses == sorted(houses, reverse=True)


def test_sort_skips_columns_the_frame_does_not_have() -> None:
    """A pivot's metric columns are named after DATA values, so a saved sort can
    reference one that this run has no column for. It is skipped, not raised."""
    spec = _rent_pivot_spec()
    spec["sort"] = [{"col": "bonds · 9999", "dir": "desc"}, {"col": "bedroom_band", "dir": "desc"}]
    code = build_object_code(object_type="pivot", spec=spec, dataset="nsw_rent")
    frame = _rent_pivot_frame()
    outcome = run_code(code, df=frame, frames={"extract": frame})
    assert outcome.error is None, outcome.error
    bands = [r["bedroom_band"] for r in outcome.report["table"]["rows"]]
    assert bands == sorted(bands, reverse=True)


def test_sort_applies_to_the_bar_family_too() -> None:
    """The x-axis order of a bar chart is a presentation choice, so the same
    sort spec drives it (this replaces the old one-off 'sort x' control)."""
    spec = {
        "grain": ["month", "postcode", "bedroom_band"],
        "dimension": "bedroom_band",
        "bar_measure": {"label": "bonds", "source": "n_rented", "agg": "sum"},
        "sort": [{"col": "bonds", "dir": "desc"}],
    }
    code = build_object_code(object_type="breakdown", spec=spec, dataset="nsw_rent")
    assert "sort_values" in code
    frame = _rent_pivot_frame()
    # Band "2" is made the larger one, so descending order is observable.
    frame.loc[frame["bedroom_band"] == "2", "n_rented"] *= 3
    outcome = run_code(code, df=frame, frames={"extract": frame})
    assert outcome.error is None, outcome.error
    vals = [v["bonds"] for v in outcome.report["main_chart"]["data"]["values"]]
    assert vals == sorted(vals, reverse=True)


def test_trend_can_plot_a_growth_rate_line() -> None:
    """A line chart OF a growth rate: the derive transforms the series per point
    (month over month), rather than collapsing it the way the bar family's
    window derive does."""
    spec = {
        "grain": ["month", "postcode", "bedroom_band"],
        "line_measure": {
            "label": "bonds",
            "source": "n_rented",
            "agg": "sum",
            "derive": "growth",
        },
        "show_actual": True,
        "rolling_window": 0,
    }
    code = build_object_code(object_type="trend", spec=spec, dataset="nsw_rent")
    # A frame whose volume doubles each month, so the growth rate is unambiguous.
    frame = pd.DataFrame(
        [
            {
                "month": f"2025-{m:02d}",
                "postcode": "2076",
                "bedroom_band": "2",
                "n_rented": 100 * (2 ** (m - 1)),
                "total_weekly_rent": 100 * (2 ** (m - 1)) * 500,
            }
            for m in range(1, 7)
        ]
    )
    outcome = run_code(code, df=frame, frames={"extract": frame})
    assert outcome.error is None, outcome.error
    pts = sorted(outcome.report["main_chart"]["data"]["values"], key=lambda p: p["month"])
    # 6 months, the first has nothing to grow from; every other is +100%.
    assert len(pts) == 5
    assert [p["value"] for p in pts] == [100.0] * 5


def test_trend_rejects_a_derive_that_is_not_a_series() -> None:
    """`latest` and `rank` reduce a series to one number, and `rolling` is the
    smoothing control — none of them is a line, so they are refused by name."""
    for derive in ("latest", "rank", "rolling"):
        spec = {
            "grain": ["month"],
            "line_measure": {
                "label": "bonds",
                "source": "n_rented",
                "agg": "sum",
                "derive": derive,
            },
        }
        with pytest.raises(ValueError, match="cannot be drawn as a line"):
            build_object_code(object_type="trend", spec=spec, dataset="nsw_rent")


def test_same_metric_twice_with_and_without_a_derive() -> None:
    """The curator's ask: a table of actuals AND growth needs the same metric
    added twice — once raw, once derived — so the labels must stay distinct and
    both columns must appear."""
    spec = _rent_pivot_spec()
    spec["pivot_measures"] = [
        {"label": "bonds", "source": "n_rented", "agg": "sum", "months": 12},
        {
            "label": "bonds growth %",
            "source": "n_rented",
            "agg": "sum",
            "months": 12,
            "derive": "growth",
        },
    ]
    code = build_object_code(object_type="pivot", spec=spec, dataset="nsw_rent")
    frame = _rent_pivot_frame()
    outcome = run_code(code, df=frame, frames={"extract": frame})
    assert outcome.error is None, outcome.error
    keys = [c["key"] for c in outcome.report["table"]["columns"]]
    assert "bonds · 2076" in keys and "bonds growth % · 2076" in keys
    # Two different numbers from the same source column — an actual and a rate.
    row = outcome.report["table"]["rows"][0]
    assert row["bonds · 2076"] != row["bonds growth % · 2076"]


def test_pivot_disambiguates_a_shared_label_across_derives() -> None:
    """Two ``pivot_measures`` sharing a label but differing only by ``derive``
    must not collide on the merge — the label is auto-disambiguated (mirroring
    the trend path's derive-suffix rename) rather than letting pandas silently
    suffix the merged column ``_x``/``_y`` and break every downstream lookup."""
    spec = _rent_pivot_spec()
    spec["pivot_measures"] = [
        {"label": "bonds", "source": "n_rented", "agg": "sum", "months": 12},
        {
            "label": "bonds",
            "source": "n_rented",
            "agg": "sum",
            "months": 12,
            "derive": "growth",
        },
    ]
    code = build_object_code(object_type="pivot", spec=spec, dataset="nsw_rent")
    frame = _rent_pivot_frame()
    outcome = run_code(code, df=frame, frames={"extract": frame})
    assert outcome.error is None, outcome.error
    keys = [c["key"] for c in outcome.report["table"]["columns"]]
    assert "bonds · 2076" in keys and "bonds growth · 2076" in keys
    row = outcome.report["table"]["rows"][0]
    assert row["bonds · 2076"] != row["bonds growth · 2076"]


def test_trend_time_derive_needs_a_time_axis() -> None:
    """A growth rate reads a change ALONG the axis. Over categories each series is
    one point, so pct_change yields nothing and the chart came back silently
    empty — say why instead. `share` compares series at each x, so it is exempt."""
    base = {
        "grain": ["month", "bedroom_band"],
        "dimension": ["bedroom_band"],
        "group": "bedroom_band",
    }
    for derive in ("growth", "yoy", "index", "cumulative"):
        spec = {
            **base,
            "line_measure": {
                "label": "bonds",
                "source": "n_rented",
                "agg": "sum",
                "derive": derive,
            },
        }
        with pytest.raises(ValueError, match="change over TIME"):
            build_object_code(object_type="trend", spec=spec, dataset="nsw_rent")

    share = {
        **base,
        "line_measure": {"label": "bonds", "source": "n_rented", "agg": "sum", "derive": "share"},
    }
    assert "transform('sum')" in build_object_code(
        object_type="trend", spec=share, dataset="nsw_rent"
    )


# ---------------------------------------------------------------------------
# Unit annotations (s34) — every number an object emits says what it IS, so the
# axis, the tooltip and the cell can't each guess differently. The reported
# defect: a YoY on avg weekly rent was annotated in dollars.
# ---------------------------------------------------------------------------
def _rent_trend_spec(**measure: Any) -> dict:
    return {
        "grain": ["month", "postcode", "bedroom_band"],
        "line_measure": {"label": "avg_weekly_rent", **measure},
        "rolling_window": 0,
    }


def _rent_trend_frame() -> pd.DataFrame:
    # Two years, so a YoY has a prior year to compare against.
    return pd.DataFrame(
        [
            {
                "month": f"{2024 + (i // 12)}-{i % 12 + 1:02d}",
                "postcode": "2076",
                "bedroom_band": "2",
                "n_rented": 100,
                "total_weekly_rent": 100 * (500 + i),
            }
            for i in range(24)
        ]
    )


def _trend_unit(spec: dict) -> str | None:
    """The y unit of the trend object a spec produces, end to end."""
    code = build_object_code(object_type="trend", spec=spec, dataset="nsw_rent")
    frame = _rent_trend_frame()
    outcome = run_code(code, df=frame, frames={"extract": frame})
    assert outcome.error is None, outcome.error
    obj = chart_object_from_spec(outcome.report["main_chart"], element_id="obj:x")
    assert obj is not None
    unit = obj.data["y_unit"]
    return None if unit is None else str(unit)


def test_a_trend_carries_its_measures_unit_not_its_columns_name() -> None:
    """The rows' value column is always literally "value" — the unit has to be
    declared or every trend, a bond count included, reads as dollars."""
    assert _trend_unit(_rent_trend_spec(num="total_weekly_rent", den="n_rented")) == "currency"
    assert (
        _trend_unit(
            {
                "grain": ["month", "postcode", "bedroom_band"],
                "line_measure": {"label": "bonds", "source": "n_rented", "agg": "sum"},
                "rolling_window": 0,
            }
        )
        == "number"
    )


def test_a_derived_trend_is_annotated_as_the_derived_value() -> None:
    """The reported bug: YoY on avg_weekly_rent is a percentage; the tooltip and
    the axis were both annotating it as the money it was derived from."""
    for derive in ("yoy", "growth"):
        spec = _rent_trend_spec(num="total_weekly_rent", den="n_rented", derive=derive)
        assert _trend_unit(spec) == "percent", derive


def test_pivot_columns_state_a_unit_per_metric() -> None:
    """Two metrics of different units sit side by side in one cross-tab, so the
    format is per generated column — a name like "bonds · 2077" can't carry it."""
    spec = _rent_pivot_spec()
    spec["pivot_measures"].append(
        {
            "label": "avg_weekly_rent growth",
            "num": "total_weekly_rent",
            "den": "n_rented",
            "derive": "growth",
            "months": 6,
        }
    )
    spec["pivot_compare"] = "pct_diff"
    code = build_object_code(object_type="pivot", spec=spec, dataset="nsw_rent")
    frame = _rent_pivot_frame()
    outcome = run_code(code, df=frame, frames={"extract": frame})
    assert outcome.error is None, outcome.error
    fmt = {c["key"]: c.get("format") for c in outcome.report["table"]["columns"]}
    assert fmt["bonds · 2076"] == "number"
    assert fmt["avg_weekly_rent · 2077"] == "currency"
    assert fmt["avg_weekly_rent growth · 2076"] == "percent"
    # A relative gap is a percentage whatever the metric it compares.
    assert fmt["avg_weekly_rent · Δ%"] == "percent"
    # The row labels are text, not numbers to annotate.
    assert fmt["bedroom_band"] is None


def test_a_raw_pivot_gap_keeps_the_metrics_own_unit() -> None:
    """A $200 difference in rent is dollars — only the % variant changes unit."""
    spec = {**_rent_pivot_spec(), "pivot_compare": "diff"}
    code = build_object_code(object_type="pivot", spec=spec, dataset="nsw_rent")
    frame = _rent_pivot_frame()
    outcome = run_code(code, df=frame, frames={"extract": frame})
    assert outcome.error is None, outcome.error
    fmt = {c["key"]: c.get("format") for c in outcome.report["table"]["columns"]}
    assert fmt["avg_weekly_rent · Δ"] == "currency"
    assert fmt["bonds · Δ"] == "number"


def test_table_columns_carry_their_measures_unit() -> None:
    spec = {
        "grain": ["month", "postcode", "bedroom_band"],
        "dimension": "bedroom_band",
        "bar_measure": {"label": "bonds", "source": "n_rented", "agg": "sum"},
        "line_measure": {
            "label": "avg_weekly_rent",
            "num": "total_weekly_rent",
            "den": "n_rented",
        },
    }
    code = build_object_code(object_type="table", spec=spec, dataset="nsw_rent")
    frame = _rent_pivot_frame()
    outcome = run_code(code, df=frame, frames={"extract": frame})
    assert outcome.error is None, outcome.error
    fmt = {c["key"]: c.get("format") for c in outcome.report["table"]["columns"]}
    assert fmt["bonds"] == "number"
    assert fmt["avg_weekly_rent"] == "currency"


def test_a_dual_axis_object_annotates_each_axis_separately() -> None:
    """The bars and the line are different measures — that is the whole reason
    for a second axis, so one being dollars says nothing about the other."""
    spec = {
        "grain": ["month", "postcode", "bedroom_band"],
        "dimension": "bedroom_band",
        "bar_measure": {"label": "bonds", "source": "n_rented", "agg": "sum"},
        "line_measure": {
            "label": "avg_weekly_rent",
            "num": "total_weekly_rent",
            "den": "n_rented",
        },
    }
    code = build_object_code(object_type="compare", spec=spec, dataset="nsw_rent")
    frame = _rent_pivot_frame()
    outcome = run_code(code, df=frame, frames={"extract": frame})
    assert outcome.error is None, outcome.error
    obj = chart_object_from_spec(outcome.report["main_chart"], element_id="obj:x")
    assert obj is not None
    assert obj.data["unit"] == "number"
    assert obj.data["line_unit"] == "currency"


def test_a_yoy_line_reaches_back_past_the_window_it_draws() -> None:
    """A YoY over the latest 12 months has no prior year INSIDE those 12 months,
    so the window has to fetch 12 more — otherwise every point is undefined and
    the chart renders empty, with no error to explain why."""
    frame = pd.DataFrame(
        [
            {
                "month": f"{2024 + (i // 12)}-{i % 12 + 1:02d}",
                "postcode": "2076",
                "bedroom_band": "2",
                "n_rented": 100,
                "total_weekly_rent": 100 * (500 + i),
            }
            for i in range(36)
        ]
    )
    spec = {
        "grain": ["month", "postcode", "bedroom_band"],
        "months": 12,
        "line_measure": {
            "label": "avg_weekly_rent",
            "num": "total_weekly_rent",
            "den": "n_rented",
            "derive": "yoy",
        },
        "rolling_window": 0,
    }
    code = build_object_code(object_type="trend", spec=spec, dataset="nsw_rent")
    outcome = run_code(code, df=frame, frames={"extract": frame})
    assert outcome.error is None, outcome.error
    points = outcome.report["main_chart"]["data"]["values"]
    # The 12 months asked for, each with a real year-ago comparison.
    assert len(points) == 12
    assert all(p["value"] > 0 for p in points)
