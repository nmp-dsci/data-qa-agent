"""The Explore aggregate service — manifest-checked requests -> parameterized SQL.

Unlike the SQL editor (which runs user-authored SQL under the agent's guardrails),
Explore requests are structured: a dataset, a metric list, a group-by list and a
filter map. Every identifier is looked up in the manifest and only its trusted SQL
`expr` reaches the query — user input only ever arrives as bound parameters, never
interpolated. Reads run under `rls_connection`, so the mart RLS scopes rows to the
caller's dataset grants regardless of what the request asks for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

from .manifest import GEO_TABLE, MART_ALIAS, Dataset, Dimension

# sqlalchemy only needed by the async runners; kept out of module scope so the SQL
# builder + validator stay importable in the dependency-light root test venv.
if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection

# Row caps: the UI never needs more than a few thousand points; CSV extract allows
# more. A month-grain all-postcode cross still can't blow past these.
UI_ROW_CAP = 5000
CSV_ROW_CAP = 50000
# A profile / trend can group by at most this many non-time dimensions at once —
# keeps an all-NSW postcode×type×month cross from being requested by accident.
MAX_GROUP_BY = 3


class ExploreValidationError(ValueError):
    """A request referenced an unknown dataset / dimension / metric / filter."""


@dataclass
class AggregateSpec:
    dataset: Dataset
    metrics: list[str]
    group_by: list[str] = field(default_factory=list)
    # dim_name -> equality scalar | list (IN) | {"min":..,"max":..} range
    filters: dict[str, Any] = field(default_factory=dict)
    limit: int = UI_ROW_CAP
    order_by_time: bool = True


def validate_spec(
    dataset: Dataset,
    metrics: list[str],
    group_by: list[str],
    filters: dict[str, Any],
) -> AggregateSpec:
    """Resolve and check every name against the manifest, or raise."""
    if not metrics:
        raise ExploreValidationError("at least one metric is required")
    for name in metrics:
        if dataset.metric(name) is None:
            raise ExploreValidationError(f"unknown metric {name!r} for dataset {dataset.slug}")
    if len(group_by) > MAX_GROUP_BY:
        raise ExploreValidationError(f"at most {MAX_GROUP_BY} group-by dimensions allowed")
    non_time = [g for g in group_by if (d := dataset.dimension(g)) and d.kind != "time"]
    if len(non_time) > MAX_GROUP_BY:
        raise ExploreValidationError(f"at most {MAX_GROUP_BY} non-time group-by dimensions allowed")
    for name in group_by:
        if dataset.dimension(name) is None:
            raise ExploreValidationError(f"unknown dimension {name!r} for dataset {dataset.slug}")
    for name, value in filters.items():
        dim = dataset.dimension(name)
        if dim is None:
            raise ExploreValidationError(f"unknown filter dimension {name!r}")
        _validate_filter_value(dim, name, value)
    return AggregateSpec(dataset=dataset, metrics=metrics, group_by=group_by, filters=dict(filters))


def _validate_filter_scalar(dim: Dimension, name: str, value: Any) -> None:
    if value is None or isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ExploreValidationError(f"invalid filter value for {name!r}: {value!r}")
    if dim.filter_kind in ("month_year", "month_fy"):
        try:
            int(value)
        except (TypeError, ValueError):
            raise ExploreValidationError(
                f"filter {name!r} requires an integer year, got {value!r}"
            ) from None


def _validate_filter_value(dim: Dimension, name: str, value: Any) -> None:
    """Check a filter value's shape/type against the dimension's filter_kind
    before it can reach _where_clause and be bound directly as a query param —
    a bad shape there surfaces as an unhandled DB type error instead of a 400."""
    if isinstance(value, dict):
        if not ("min" in value or "max" in value):
            raise ExploreValidationError(f"invalid range filter for {name!r}")
        for key in ("min", "max"):
            if key in value and value[key] is not None:
                _validate_filter_scalar(dim, name, value[key])
    elif isinstance(value, (list, tuple, set)):
        for v in value:
            _validate_filter_scalar(dim, name, v)
    else:
        _validate_filter_scalar(dim, name, value)


def _needs_geo(spec: AggregateSpec) -> bool:
    names = set(spec.group_by) | set(spec.filters)
    return any((d := spec.dataset.dimension(n)) and d.needs_geo_join for n in names)


def _where_clause(spec: AggregateSpec) -> tuple[str, dict[str, Any]]:
    """Build the WHERE fragment and its bound params from the filter map."""
    parts: list[str] = []
    params: dict[str, Any] = {}
    i = 0
    for name, value in spec.filters.items():
        dim = spec.dataset.dimension(name)
        assert dim is not None  # validated
        expr = dim.expr
        # Sargable year / financial-year equality: translate a year value into a
        # half-open range on the indexed month column instead of comparing a
        # full-scan extract() expression.
        if (
            dim.filter_kind in ("month_year", "month_fy")
            and dim.filter_col
            and not isinstance(value, (dict, list, tuple, set))
        ):
            try:
                year = int(value)
            except (TypeError, ValueError):
                year = None
            if year is not None:
                lo_key, hi_key = f"p{i}", f"p{i + 1}"
                col = f"{MART_ALIAS}.{dim.filter_col}"
                if dim.filter_kind == "month_year":
                    params[lo_key] = date(year, 1, 1)
                    params[hi_key] = date(year + 1, 1, 1)
                else:  # month_fy: FY ending June (Jul (Y-1) .. Jun Y)
                    params[lo_key] = date(year - 1, 7, 1)
                    params[hi_key] = date(year, 7, 1)
                parts.append(f"{col} >= :{lo_key} and {col} < :{hi_key}")
                i += 2
                continue
        if isinstance(value, dict) and ("min" in value or "max" in value):
            if value.get("min") is not None:
                key = f"p{i}"
                params[key] = value["min"]
                parts.append(f"{expr} >= :{key}")
                i += 1
            if value.get("max") is not None:
                key = f"p{i}"
                params[key] = value["max"]
                parts.append(f"{expr} <= :{key}")
                i += 1
        elif isinstance(value, (list, tuple, set)):
            vals = list(value)
            if not vals:
                parts.append("false")  # an empty IN () matches nothing
                continue
            keys = []
            for v in vals:
                key = f"p{i}"
                params[key] = v
                keys.append(f":{key}")
                i += 1
            parts.append(f"{expr} in ({', '.join(keys)})")
        else:
            key = f"p{i}"
            params[key] = value
            parts.append(f"{expr} = :{key}")
            i += 1
    clause = " and ".join(parts) if parts else ""
    return clause, params


def build_aggregate_sql(spec: AggregateSpec) -> tuple[str, dict[str, Any]]:
    """Compose the aggregate SELECT and its bound params. Dimensions are selected
    first (so GROUP BY / ORDER BY can reference them positionally), then metrics."""
    ds = spec.dataset
    select_parts: list[str] = []
    for name in spec.group_by:
        dim = ds.dimension(name)
        assert dim is not None
        select_parts.append(f"{dim.expr} as {name}")
    for name in spec.metrics:
        mtr = ds.metric(name)
        assert mtr is not None
        select_parts.append(f"{mtr.expr} as {name}")

    from_clause = f"{ds.table} m"
    if _needs_geo(spec):
        from_clause += f" left join {GEO_TABLE} g on g.postcode = m.postcode"

    where_clause, params = _where_clause(spec)
    sql = f"select {', '.join(select_parts)}\nfrom {from_clause}"
    if where_clause:
        sql += f"\nwhere {where_clause}"
    if spec.group_by:
        positions = ", ".join(str(i + 1) for i in range(len(spec.group_by)))
        sql += f"\ngroup by {positions}"
        # Order by the time dimension ascending when present (trends need it
        # sorted), else by the grouped columns for a stable output.
        sql += f"\norder by {positions}"

    effective_limit = max(1, min(spec.limit, CSV_ROW_CAP))
    # Fetch one extra to detect truncation.
    sql += f"\nlimit {effective_limit + 1}"
    return sql, params


async def granted_dataset_slugs(conn: AsyncConnection) -> set[str]:
    """Slugs the current RLS user may read (admins see all)."""
    from sqlalchemy import text

    rows = (
        await conn.execute(
            text(
                "SELECT d.slug FROM app.datasets d "
                "WHERE app.is_admin() OR EXISTS ("
                "  SELECT 1 FROM app.dataset_access da "
                "  WHERE da.dataset_id = d.id AND da.user_id = app.current_user_id()"
                ")"
            )
        )
    ).all()
    return {r[0] for r in rows}


async def run_aggregate(conn: AsyncConnection, spec: AggregateSpec) -> dict[str, Any]:
    """Execute an aggregate and return columns/rows/row_count/truncated/sql/params."""
    from sqlalchemy import text

    sql, params = build_aggregate_sql(spec)
    result = await conn.execute(text(sql), params)
    columns = list(result.keys())
    all_rows = result.fetchall()
    effective_limit = max(1, min(spec.limit, CSV_ROW_CAP))
    truncated = len(all_rows) > effective_limit
    rows = all_rows[:effective_limit]
    from ..db import jsonable

    return {
        "columns": columns,
        "rows": [[jsonable(v) for v in row] for row in rows],
        "row_count": len(rows),
        "truncated": truncated,
        "sql": sql,
        "params": params,
    }


async def cohort_totals(
    conn: AsyncConnection, dataset: Dataset, filters: dict[str, Any]
) -> tuple[dict[str, Any], str, int]:
    """All metrics for a cohort at the grand-total level (group_by = []).

    Returns (result, sql, row_count) — the sql/row_count let the caller audit
    this query in app.query_runs the same way the SQL editor is audited."""
    from sqlalchemy import text

    metrics = [m.name for m in dataset.metrics]
    spec = validate_spec(dataset, metrics, [], filters)
    sql, params = build_aggregate_sql(spec)
    row = (await conn.execute(text(sql), params)).mappings().first()
    result = dict(row) if row else {m: None for m in metrics}
    return result, sql, 1 if row else 0


async def cohort_by_predictor(
    conn: AsyncConnection,
    dataset: Dataset,
    predictor: str,
    response_metric: str,
    volume_metric: str,
    filters: dict[str, Any],
) -> tuple[list[dict[str, Any]], str, dict[str, Any], int]:
    """A cohort's response metric per segment of one predictor, with a volume count.

    Returns (rows, sql, params, row_count); rows are shaped for the engine:
    {segment, <response_metric>, _n}. sql/row_count let the caller audit this
    query in app.query_runs the same way the SQL editor is audited; sql/params
    together let the caller build a runnable "open in SQL editor" query.
    """
    from sqlalchemy import text

    metrics = (
        [response_metric] if volume_metric == response_metric else [response_metric, volume_metric]
    )
    spec = validate_spec(dataset, metrics, [predictor], filters)
    sql, params = build_aggregate_sql(spec)
    rows = (await conn.execute(text(sql), params)).mappings().all()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["segment"] = d.pop(predictor)
        d["_n"] = d.get(volume_metric)
        out.append(d)
    return out, sql, params, len(out)
