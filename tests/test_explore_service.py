"""Explore aggregate SQL builder + request validation (pure, no DB).

Verifies the manifest-checked contract: only allow-listed identifiers reach SQL,
user values arrive as bound params, and geo rollups add the dim_postcode_geo join.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "backend-api"))

from app.explore import service  # noqa: E402
from app.explore.manifest import get_dataset  # noqa: E402


def _sales():
    ds = get_dataset("nsw_sales")
    assert ds is not None
    return ds


def test_basic_aggregate_sql_and_derived_metric_expr() -> None:
    ds = _sales()
    spec = service.validate_spec(ds, ["avg_sale_price"], ["month"], {})
    sql, params = service.build_aggregate_sql(spec)
    assert "from marts.property_sales m" in sql
    assert "m.month as month" in sql
    # derived ratio-of-sums, not an average-of-averages
    assert "sum(m.total_sale_value) / nullif(sum(m.n_sold), 0) as avg_sale_price" in sql
    assert "group by 1" in sql
    assert "order by 1" in sql
    assert sql.strip().endswith("limit 5001")  # UI cap + 1 for truncation probe
    assert params == {}


def test_geo_rollup_adds_join() -> None:
    ds = _sales()
    spec = service.validate_spec(ds, ["n_sold"], ["sa3_region"], {})
    sql, _ = service.build_aggregate_sql(spec)
    assert "left join marts.dim_postcode_geo g on g.postcode = m.postcode" in sql
    assert "g.sa3_name as sa3_region" in sql


def test_equality_filter_is_parameterized() -> None:
    ds = _sales()
    spec = service.validate_spec(ds, ["n_sold"], [], {"property_type": "house"})
    sql, params = service.build_aggregate_sql(spec)
    assert "where m.property_type = :p0" in sql
    assert params == {"p0": "house"}


def test_range_filter_min_and_max() -> None:
    ds = _sales()
    spec = service.validate_spec(ds, ["n_sold"], [], {"year_fy": {"min": 2021, "max": 2022}})
    sql, params = service.build_aggregate_sql(spec)
    # year_fy is a computed FY expression, compared to bound params
    assert ">= :p0" in sql and "<= :p1" in sql
    assert params == {"p0": 2021, "p1": 2022}


def test_in_filter_lists_all_values() -> None:
    ds = _sales()
    spec = service.validate_spec(ds, ["n_sold"], [], {"postcode": ["2000", "2010"]})
    sql, params = service.build_aggregate_sql(spec)
    assert "m.postcode in (:p0, :p1)" in sql
    assert params == {"p0": "2000", "p1": "2010"}


def test_financial_year_filter_is_a_sargable_month_range() -> None:
    from datetime import date

    ds = _sales()
    spec = service.validate_spec(ds, ["n_sold"], [], {"year_fy": 2022})
    sql, params = service.build_aggregate_sql(spec)
    # FY2022 = Jul 2021 .. Jun 2022, as a half-open range on the indexed month col.
    assert "m.month >= :p0 and m.month < :p1" in sql
    assert params == {"p0": date(2021, 7, 1), "p1": date(2022, 7, 1)}
    assert "extract(" not in sql.split("where")[1]  # not the full-scan expression


def test_calendar_year_filter_is_a_sargable_month_range() -> None:
    from datetime import date

    ds = _sales()
    spec = service.validate_spec(ds, ["n_sold"], [], {"year": 2023})
    sql, params = service.build_aggregate_sql(spec)
    assert "m.month >= :p0 and m.month < :p1" in sql
    assert params == {"p0": date(2023, 1, 1), "p1": date(2024, 1, 1)}


def test_unknown_metric_rejected() -> None:
    ds = _sales()
    with pytest.raises(service.ExploreValidationError):
        service.validate_spec(ds, ["1; drop table app.users"], [], {})


def test_unknown_dimension_rejected() -> None:
    ds = _sales()
    with pytest.raises(service.ExploreValidationError):
        service.validate_spec(ds, ["n_sold"], ["ssn"], {})


def test_unknown_filter_dimension_rejected() -> None:
    ds = _sales()
    with pytest.raises(service.ExploreValidationError):
        service.validate_spec(ds, ["n_sold"], [], {"evil": 1})


def test_too_many_group_by_rejected() -> None:
    ds = _sales()
    with pytest.raises(service.ExploreValidationError):
        service.validate_spec(
            ds, ["n_sold"], ["postcode", "suburb", "zoning", "property_type"], {}
        )


def test_empty_metrics_rejected() -> None:
    ds = _sales()
    with pytest.raises(service.ExploreValidationError):
        service.validate_spec(ds, [], ["month"], {})
