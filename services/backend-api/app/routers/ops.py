"""The /ops flight deck's endpoints (s32 W0) — read the rollup, ingest outcomes.

Two halves with deliberately different auth:

* **Read** (``GET /admin/ops/summary``) — admin session, reads one pre-aggregated
  ``app.ops_rollup`` row (decision Q3). If the row is missing or stale it kicks a
  background refresh and still answers immediately with what it has, plus the
  ``refreshed_at`` that makes the lag visible. A dashboard that blocks on its own
  aggregation is a dashboard that times out.

* **Ingest** (``POST /ops/ingest/{kind}``) — a machine token, because the writers
  are not browsers: a k6 results-writer, a promptfoo results-writer, and the
  deploy workflow. None of them can reach Aurora directly (its security group
  admits the ECS jobs, App Runner's egress ranges, and operator CIDRs — not
  GitHub Actions runners), and none of them has a user session. One narrow,
  append-only, token-gated endpoint on the service that *can* reach the database
  is the honest shape. ``OPS_INGEST_TOKEN`` empty — the default — disables the
  path entirely rather than leaving it open.

Everything the ingest path writes is an operational record with no user data in
it, and every field is bound, never interpolated.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from .. import ops_rollup
from ..auth import CurrentUser, require_admin
from ..config import settings
from ..db import rls_connection

router = APIRouter(tags=["ops"])
log = logging.getLogger("uvicorn.error")


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

# asyncio.create_task only keeps a weak reference to the task; without holding
# one here a background refresh can be garbage-collected before it runs.
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


async def _refresh_in_background(window_key: str) -> None:
    """Recompute one window off the request path; failures are logged, not raised."""
    try:
        await ops_rollup.refresh_window(window_key)
    except Exception as exc:  # noqa: BLE001 — a stale deck beats a broken one
        log.warning("ops rollup background refresh failed for %s: %s", window_key, exc)


@router.get("/admin/ops/summary")
async def ops_summary(
    window: str = ops_rollup.DEFAULT_WINDOW,
    admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """One window of deck metrics, served from the rollup.

    Never scans raw ``query_runs``: on a cold or stale rollup the response says
    so (``stale: true``, ``refreshed_at: null``) and a background task fills it
    in, so the next poll — the deck polls on an interval — shows real numbers.
    The first ever load therefore renders empty panels rather than hanging on a
    3M-row percentile scan.
    """
    key = window if window in ops_rollup.WINDOWS else ops_rollup.DEFAULT_WINDOW
    async with rls_connection(admin.id) as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT refreshed_at, metrics, "
                        "  EXTRACT(EPOCH FROM (now() - refreshed_at)) AS age_s "
                        "FROM app.ops_rollup WHERE window_key = :key"
                    ),
                    {"key": key},
                )
            )
            .mappings()
            .first()
        )

    age_s = float(row["age_s"]) if row and row["age_s"] is not None else None
    stale = row is None or (age_s is not None and age_s > ops_rollup.STALE_AFTER_S)
    if stale:
        # Fire-and-forget: the caller gets what exists now, fresh numbers next poll.
        task = asyncio.create_task(_refresh_in_background(key))
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)

    return {
        "window": key,
        "windows": list(ops_rollup.WINDOWS),
        "refreshed_at": row["refreshed_at"].isoformat() if row else None,
        "age_s": round(age_s, 1) if age_s is not None else None,
        "stale": stale,
        "metrics": (row["metrics"] if row else {}) or {},
    }


@router.post("/admin/ops/refresh")
async def ops_refresh(admin: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Recompute every window now (the deck's "refresh" control).

    Synchronous on purpose: the admin asked for fresh numbers and should wait
    for them, unlike the poll path. ``admin`` is the authorization check only —
    the aggregate itself reads through admin_ro, so this and the background/
    scheduler paths compute exactly the same numbers.
    """
    del admin  # gate only; refresh_all owns its own connections
    refreshed = await ops_rollup.refresh_all()
    return {"refreshed": refreshed}


@router.get("/admin/ops/runs")
async def ops_runs(
    limit: int = 25,
    admin: CurrentUser = Depends(require_admin),
) -> list[dict[str, Any]]:
    """The slowest recent asks, each deep-linked to its Logfire trace.

    The deck is the outcomes plane and Logfire the microscope: a slow row here
    is one click from its span waterfall via ``otel_trace_id``. Ordered by
    latency rather than time because "what was slow" is the question this panel
    exists to answer.
    """
    async with rls_connection(admin.id) as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT qr.id, qr.created_at, qr.latency_ms, qr.ttfp_ms, qr.status, "
                        "  qr.degraded, qr.attempts, qr.cost_usd, qr.otel_trace_id, qr.engine, "
                        "  left(qr.question, 90) AS question, u.username "
                        "FROM app.query_runs qr JOIN app.users u ON u.id = qr.user_id "
                        "WHERE qr.source = 'agent' AND qr.created_at >= now() - interval '7 days' "
                        "ORDER BY qr.latency_ms DESC NULLS LAST LIMIT :lim"
                    ),
                    {"lim": max(1, min(limit, 100))},
                )
            )
            .mappings()
            .all()
        )
    return [
        {
            "id": str(r["id"]),
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "latency_ms": r["latency_ms"],
            "ttfp_ms": r["ttfp_ms"],
            "status": r["status"],
            "degraded": r["degraded"],
            "attempts": r["attempts"],
            "cost_usd": float(r["cost_usd"]) if r["cost_usd"] is not None else None,
            "otel_trace_id": r["otel_trace_id"],
            "engine": r["engine"],
            "question": r["question"],
            "username": r["username"],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Ingest (machine token)
# ---------------------------------------------------------------------------


async def require_ops_token(x_ops_token: str | None = Header(default=None)) -> None:
    """Gate the ingest path on ``OPS_INGEST_TOKEN``; unset = the path is closed.

    Compared with ``compare_digest`` so a wrong token can't be found a byte at a
    time, mirroring the agent's shared-token middleware.
    """
    expected = settings.ops_ingest_token
    if not expected:
        raise HTTPException(status_code=404, detail="Ops ingest is not enabled")
    if not x_ops_token or not secrets.compare_digest(x_ops_token.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="invalid X-Ops-Token")


class LoadTestIn(BaseModel):
    scenario: str = ""
    vus: int | None = None
    duration_s: int | None = None
    rps: float | None = None
    p50_ms: int | None = None
    p95_ms: int | None = None
    p99_ms: int | None = None
    error_rate: float | None = None
    git_sha: str | None = None
    notes: str | None = None


class SecurityRunIn(BaseModel):
    kind: Literal["redteam", "injection"] = "redteam"
    pack_sha: str | None = None
    total: int = 0
    passed: int = 0
    by_category: dict[str, Any] = Field(default_factory=dict)
    report_url: str | None = None


class DeployEventIn(BaseModel):
    """Start or finish a deploy record.

    The workflow posts once at the start (no ``status``, so 'running') and once
    at the end with the same ``git_sha`` and a terminal status; the second post
    updates the first rather than writing a second row, keyed on the most recent
    running row for that sha.
    """

    git_sha: str
    actor: str | None = None
    status: Literal["running", "deployed", "rolled_back", "failed"] = "running"
    smoke: dict[str, Any] = Field(default_factory=dict)
    duration_s: int | None = None
    notes: str | None = None


class PipelineRunIn(BaseModel):
    status: Literal["running", "success", "failed"] = "success"
    duration_s: int | None = None
    marts_refreshed_at: datetime | None = None
    dbt_pass: int | None = None
    dbt_total: int | None = None
    row_counts: dict[str, Any] = Field(default_factory=dict)
    git_sha: str | None = None
    source: str | None = None
    notes: str | None = None


@router.post("/ops/ingest/load-test", status_code=201, dependencies=[Depends(require_ops_token)])
async def ingest_load_test(body: LoadTestIn) -> dict[str, str]:
    async with rls_connection(None) as conn:
        run_id = (
            await conn.execute(
                text(
                    "INSERT INTO app.load_tests (scenario, vus, duration_s, rps, p50_ms, "
                    "  p95_ms, p99_ms, error_rate, git_sha, notes) "
                    "VALUES (:scenario, :vus, :duration_s, :rps, :p50_ms, :p95_ms, :p99_ms, "
                    "  :error_rate, :git_sha, :notes) RETURNING id"
                ),
                body.model_dump(),
            )
        ).scalar_one()
    return {"id": str(run_id)}


@router.post("/ops/ingest/security-run", status_code=201, dependencies=[Depends(require_ops_token)])
async def ingest_security_run(body: SecurityRunIn) -> dict[str, str]:
    params = body.model_dump()
    params["by_category"] = json.dumps(params["by_category"])
    async with rls_connection(None) as conn:
        run_id = (
            await conn.execute(
                text(
                    "INSERT INTO app.security_runs (kind, pack_sha, total, passed, by_category, "
                    "  report_url) VALUES (:kind, :pack_sha, :total, :passed, "
                    "  CAST(:by_category AS jsonb), :report_url) RETURNING id"
                ),
                params,
            )
        ).scalar_one()
    return {"id": str(run_id)}


@router.post("/ops/ingest/deploy", status_code=201, dependencies=[Depends(require_ops_token)])
async def ingest_deploy(body: DeployEventIn) -> dict[str, str]:
    params = body.model_dump()
    params["smoke"] = json.dumps(params["smoke"])
    async with rls_connection(None) as conn:
        if body.status == "running":
            event_id = (
                await conn.execute(
                    text(
                        "INSERT INTO app.deploy_events (git_sha, actor, status, smoke, notes) "
                        "VALUES (:git_sha, :actor, 'running', CAST(:smoke AS jsonb), :notes) "
                        "RETURNING id"
                    ),
                    params,
                )
            ).scalar_one()
            return {"id": str(event_id), "action": "started"}

        # Close the open record for this sha. duration_s is derived from
        # started_at when the caller doesn't supply it, so a workflow only has
        # to post twice and never has to do arithmetic.
        event_id = (
            await conn.execute(
                text(
                    "UPDATE app.deploy_events SET finished_at = now(), status = :status, "
                    "  smoke = CAST(:smoke AS jsonb), notes = coalesce(:notes, notes), "
                    "  duration_s = coalesce(:duration_s, "
                    "    CAST(EXTRACT(EPOCH FROM (now() - started_at)) AS integer)) "
                    "WHERE id = (SELECT id FROM app.deploy_events "
                    "  WHERE git_sha = :git_sha AND status = 'running' "
                    "  ORDER BY started_at DESC LIMIT 1) RETURNING id"
                ),
                params,
            )
        ).scalar()
        if event_id is None:
            # No open record (a manual rollback, or a start post that never
            # landed) — record the outcome standalone rather than losing it.
            event_id = (
                await conn.execute(
                    text(
                        "INSERT INTO app.deploy_events (git_sha, actor, status, smoke, "
                        "  duration_s, notes, finished_at) "
                        "VALUES (:git_sha, :actor, :status, CAST(:smoke AS jsonb), "
                        "  :duration_s, :notes, now()) RETURNING id"
                    ),
                    params,
                )
            ).scalar_one()
            return {"id": str(event_id), "action": "recorded"}
    return {"id": str(event_id), "action": "finished"}


@router.post("/ops/ingest/pipeline-run", status_code=201, dependencies=[Depends(require_ops_token)])
async def ingest_pipeline_run(body: PipelineRunIn) -> dict[str, str]:
    params = body.model_dump()
    params["row_counts"] = json.dumps(params["row_counts"])
    async with rls_connection(None) as conn:
        run_id = (
            await conn.execute(
                text(
                    "INSERT INTO app.pipeline_runs (status, duration_s, marts_refreshed_at, "
                    "  dbt_pass, dbt_total, row_counts, git_sha, source, notes) "
                    "VALUES (:status, :duration_s, :marts_refreshed_at, :dbt_pass, :dbt_total, "
                    "  CAST(:row_counts AS jsonb), :git_sha, :source, :notes) RETURNING id"
                ),
                params,
            )
        ).scalar_one()
    return {"id": str(run_id)}


@router.post("/ops/ingest/rollup", dependencies=[Depends(require_ops_token)])
async def ingest_rollup_refresh() -> dict[str, Any]:
    """Recompute every window — the hook a scheduler (cron / EventBridge) calls.

    Same work as ``POST /admin/ops/refresh``, reachable with the machine token
    so refreshing the deck never requires a human session.
    """
    refreshed = await ops_rollup.refresh_all()
    return {"refreshed": refreshed}
