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

import pandas as pd
import pytest

from agent.object_builder import (
    build_object_code,
    canonical_extract_sql,
    element_id_for,
    needed_columns,
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
