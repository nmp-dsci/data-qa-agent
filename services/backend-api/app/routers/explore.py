"""Explore endpoints: datasets/manifest, aggregate, profile, and NL setup.

All reads run under `rls_connection`, so the mart RLS scopes rows to the caller's
dataset grants. Requests are manifest-checked (service.validate_spec) — only
allow-listed identifiers reach SQL; user input arrives only as bound parameters.
Aggregate reads are audited in app.query_runs (source='explore'), like the SQL
editor.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from ..auth import CurrentUser, get_current_user
from ..channel import get_channel
from ..db import jsonable, rls_connection
from ..explore import engine, nl_setup, service
from ..explore.manifest import Dataset, get_dataset
from ..explore.pages_builder import build_profile_pages

router = APIRouter(prefix="/explore", tags=["explore"])

# Dimensions too high-cardinality to ship a full domain for — the UI uses the
# typeahead endpoint for these instead.
_TYPEAHEAD_DIMS = {"postcode", "suburb"}
_DOMAIN_CAP = 100
# Concurrency for the per-dimension domain/time queries in /datasets. Each opens
# its own short-lived connection so the ~30 tiny aggregates run in parallel
# instead of serially (39s -> a few seconds on the full marts).
_DOMAIN_CONCURRENCY = 8
# The /datasets payload (manifest + domains) only changes when the pipeline
# rebuilds, so cache it per granted-set for a few minutes. First load pays the
# cost; the rest are instant.
_DATASETS_TTL_S = 300.0
_datasets_cache: dict[frozenset[str], tuple[float, list[dict[str, Any]]]] = {}


def _count_metric(dataset: Dataset) -> str:
    for m in dataset.metrics:
        if m.kind == "additive" and m.fmt == "number":
            return m.name
    return dataset.metrics[0].name


async def _require_dataset(conn: Any, slug: str) -> Dataset:
    dataset = get_dataset(slug)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Unknown dataset {slug!r}")
    granted = await service.granted_dataset_slugs(conn)
    if slug not in granted:
        raise HTTPException(status_code=403, detail=f"No access to dataset {slug!r}")
    return dataset


async def _log_event(conn: Any, user_id: str, event_type: str, payload: dict[str, Any]) -> None:
    await conn.execute(
        text(
            "INSERT INTO app.events (user_id, event_type, payload) "
            "VALUES (:uid, :etype, CAST(:payload AS jsonb))"
        ),
        {"uid": user_id, "etype": event_type, "payload": json.dumps(payload)},
    )


# ---------------------------------------------------------------------------
# GET /explore/datasets — granted datasets + manifest + dim domains + time range
# ---------------------------------------------------------------------------
@router.get("/datasets")
async def list_datasets(user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    async with rls_connection(user.id) as conn:
        granted = await service.granted_dataset_slugs(conn)
    key = frozenset(s for s in granted if get_dataset(s) is not None)

    cached = _datasets_cache.get(key)
    if cached and (time.monotonic() - cached[0]) < _DATASETS_TTL_S:
        return {"datasets": cached[1]}

    # The domain + time-range probes are independent tiny aggregates; run them
    # concurrently (each on its own connection) instead of serially.
    sem = asyncio.Semaphore(_DOMAIN_CONCURRENCY)

    async def domain(dataset: Dataset, dim_name: str) -> tuple[str, str, list[dict[str, Any]]]:
        async with sem, rls_connection(user.id) as c:
            spec = service.validate_spec(dataset, [_count_metric(dataset)], [dim_name], {})
            spec.limit = _DOMAIN_CAP
            result = await service.run_aggregate(c, spec)
        # Carry the per-value count so the UI can draw a distribution bar behind
        # each option (how much data toggling a value turns on/off).
        ordered = sorted(result["rows"], key=lambda r: -(r[1] or 0))
        return (
            dataset.slug,
            dim_name,
            [{"value": r[0], "count": r[1]} for r in ordered if r[0] is not None],
        )

    async def time_range(dataset: Dataset) -> tuple[str, dict[str, Any]]:
        dim = dataset.dimension(dataset.time_dim)
        if dim is None:
            return dataset.slug, {"min": None, "max": None}
        sql = f"select min({dim.expr}) as lo, max({dim.expr}) as hi from {dataset.table} m"
        async with sem, rls_connection(user.id) as c:
            row = (await c.execute(text(sql))).mappings().first()
        return dataset.slug, {
            "min": jsonable(row["lo"]) if row else None,
            "max": jsonable(row["hi"]) if row else None,
        }

    datasets = [get_dataset(s) for s in key]
    domain_tasks = [
        domain(ds, d.name)
        for ds in datasets
        if ds
        for d in ds.dimensions
        if d.kind != "time" and d.name not in _TYPEAHEAD_DIMS
    ]
    time_tasks = [time_range(ds) for ds in datasets if ds]
    domain_results = await asyncio.gather(*domain_tasks)
    time_results = await asyncio.gather(*time_tasks)

    domains: dict[tuple[str, str], list[Any]] = {(s, n): vals for s, n, vals in domain_results}
    ranges: dict[str, dict[str, Any]] = dict(time_results)

    out: list[dict[str, Any]] = []
    for ds in datasets:
        if ds is None:
            continue
        public = ds.to_public()
        public["dimensions"] = [
            {
                "name": d.name,
                "label": d.label,
                "kind": d.kind,
                "source": d.source,
                "ordinal": d.is_ordinal,
                "unit": d.unit,
                "domain": None
                if d.kind == "time" or d.name in _TYPEAHEAD_DIMS
                else domains.get((ds.slug, d.name), []),
                "typeahead": d.name in _TYPEAHEAD_DIMS,
                # Multi-selectable (IN filter) — categorical/geo dims. Year/FY stay
                # single-select (one period per cohort, and single-value keeps the
                # sargable month-range filter); time dims are free-form.
                "multi": d.filter_kind == "eq" and d.kind != "time",
            }
            for d in ds.dimensions
        ]
        public["time_range"] = ranges.get(ds.slug, {"min": None, "max": None})
        out.append(public)
    out.sort(key=lambda d: d["slug"])
    _datasets_cache[key] = (time.monotonic(), out)
    return {"datasets": out}


# ---------------------------------------------------------------------------
# GET /explore/typeahead — the high-cardinality dimension tail (postcode/suburb)
# ---------------------------------------------------------------------------
@router.get("/typeahead")
async def typeahead(
    dataset: str,
    dimension: str,
    q: str = "",
    limit: int = 20,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    async with rls_connection(user.id) as conn:
        ds = await _require_dataset(conn, dataset)
        dim = ds.dimension(dimension)
        if dim is None:
            raise HTTPException(status_code=400, detail=f"Unknown dimension {dimension!r}")
        limit = max(1, min(limit, 50))
        needs_geo = dim.needs_geo_join
        join = f" left join {service.GEO_TABLE} g on g.postcode = m.postcode" if needs_geo else ""
        where = f"where {dim.expr} ilike :q" if q else ""
        sql = (
            f"select distinct {dim.expr} as v from {ds.table} m{join} "
            f"{where} order by v limit {limit}"
        )
        params = {"q": f"%{q}%"} if q else {}
        rows = (await conn.execute(text(sql), params)).all()
    return {"values": [r[0] for r in rows if r[0] is not None]}


# ---------------------------------------------------------------------------
# POST /explore/aggregate — the workhorse for Trends and the extract
# ---------------------------------------------------------------------------
class AggregateBody(BaseModel):
    dataset: str
    metrics: list[str]
    group_by: list[str] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)
    limit: int = service.UI_ROW_CAP


@router.post("/aggregate")
async def aggregate(
    body: AggregateBody,
    user: CurrentUser = Depends(get_current_user),
    channel: str = Depends(get_channel),
) -> dict[str, Any]:
    started = time.perf_counter()
    async with rls_connection(user.id) as conn:
        dataset = await _require_dataset(conn, body.dataset)
        try:
            spec = service.validate_spec(dataset, body.metrics, body.group_by, body.filters)
        except service.ExploreValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        spec.limit = body.limit
        result = await service.run_aggregate(conn, spec)
        latency_ms = int((time.perf_counter() - started) * 1000)
        await _audit(conn, user.id, result["sql"], result["row_count"], latency_ms, channel)
        await _log_event(
            conn,
            user.id,
            "explore_aggregate",
            {"dataset": body.dataset, "metrics": body.metrics, "group_by": body.group_by},
        )
    return {
        "columns": result["columns"],
        "rows": result["rows"],
        "row_count": result["row_count"],
        "truncated": result["truncated"],
        "latency_ms": latency_ms,
        # The exact parameterized SQL (params inlined for readability) so a chart
        # can offer "open this query in the SQL editor".
        "sql": _inline_sql(result["sql"], spec),
    }


def _inline_sql(sql: str, spec: service.AggregateSpec) -> str:
    """Render the parameterized SQL with its filter values inlined — a readable,
    runnable query for the SQL editor. Longest param names first so ``:p1`` never
    partially matches inside ``:p10``."""
    _, params = service.build_aggregate_sql(spec)
    out = sql
    for key in sorted(params, key=len, reverse=True):
        val = params[key]
        literal = str(val) if isinstance(val, (int, float)) else f"'{val}'"
        out = out.replace(f":{key}", literal)
    return out


async def _audit(
    conn: Any, user_id: str, sql: str, row_count: int, latency_ms: int, channel: str
) -> None:
    await conn.execute(
        text(
            "INSERT INTO app.query_runs "
            "(user_id, sql_text, engine, row_count, latency_ms, status, source, channel) "
            "VALUES (:uid, :sql, 'explore', :rc, :lat, 'success', 'explore', :channel)"
        ),
        {"uid": user_id, "sql": sql, "rc": row_count, "lat": latency_ms, "channel": channel},
    )


# ---------------------------------------------------------------------------
# POST /explore/profile — the cohort comparison + per-predictor uplift ranking
# ---------------------------------------------------------------------------
class CohortBody(BaseModel):
    filters: dict[str, Any] = Field(default_factory=dict)


class ProfileBody(BaseModel):
    dataset: str
    metric: str | None = None
    target: CohortBody
    comparison: CohortBody


@router.post("/profile")
async def profile(
    body: ProfileBody, user: CurrentUser = Depends(get_current_user)
) -> dict[str, Any]:
    async with rls_connection(user.id) as conn:
        dataset = await _require_dataset(conn, body.dataset)
        response_metric = body.metric or dataset.default_metric
        if dataset.metric(response_metric) is None:
            raise HTTPException(status_code=400, detail=f"Unknown metric {response_metric!r}")
        try:
            # Validate cohort filters up front (both sides).
            service.validate_spec(dataset, [response_metric], [], body.target.filters)
            service.validate_spec(dataset, [response_metric], [], body.comparison.filters)
        except service.ExploreValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        count_metric = _count_metric(dataset)
        target_totals = await service.cohort_totals(conn, dataset, body.target.filters)
        comparison_totals = await service.cohort_totals(conn, dataset, body.comparison.filters)

        target_by: dict[str, list[dict[str, Any]]] = {}
        comparison_by: dict[str, list[dict[str, Any]]] = {}
        for dim in dataset.predictor_dimensions:
            target_by[dim.name] = await service.cohort_by_predictor(
                conn, dataset, dim.name, response_metric, count_metric, body.target.filters
            )
            comparison_by[dim.name] = await service.cohort_by_predictor(
                conn, dataset, dim.name, response_metric, count_metric, body.comparison.filters
            )

        await _log_event(
            conn,
            user.id,
            "explore_profile",
            {"dataset": body.dataset, "metric": response_metric},
        )

    result = engine.build_profile(
        dataset,
        response_metric,
        target_totals,
        comparison_totals,
        target_by,
        comparison_by,
    )
    payload = result.to_public()
    payload["dataset"] = dataset.slug
    payload["target_filters"] = body.target.filters
    payload["comparison_filters"] = body.comparison.filters
    payload["geo"] = (
        {"dimension": dataset.geo.dimension, "layer": dataset.geo.layer} if dataset.geo else None
    )
    # The same result assembled as report-engine pages (s20): the UI renders
    # these through PageLayout, and Save-as-golden persists them unchanged.
    payload["pages"] = build_profile_pages(payload, {d.name: d.label for d in dataset.dimensions})
    return payload


# ---------------------------------------------------------------------------
# POST /explore/ask — natural language -> populated tool state
# ---------------------------------------------------------------------------
class AskBody(BaseModel):
    question: str
    mode: str = "profile"  # profile | trends
    dataset: str | None = None


@router.post("/ask")
async def ask(body: AskBody, user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="A question is required")
    async with rls_connection(user.id) as conn:
        granted = await service.granted_dataset_slugs(conn)
        forced = None
        if body.dataset:
            forced = await _require_dataset(conn, body.dataset)
        await _log_event(conn, user.id, "explore_ask", {"mode": body.mode})
    try:
        if body.mode == "trends":
            state = nl_setup.interpret_trends(question, granted, forced)
        else:
            state = nl_setup.interpret_profile(question, granted, forced)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"mode": body.mode, "state": state}
