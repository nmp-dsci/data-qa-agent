"""Compute the /ops flight deck's windowed metrics (s32 W0, decision Q3).

The deck reads one pre-aggregated row per window from ``app.ops_rollup``; this
module is what writes those rows. Every heavy scan — the ``percentile_cont``
over ``query_runs``, the DAU distinct-count, the cost sum — happens here, in a
refresh, never on a request. That is the whole point of Q3: the audit is already
past 3M rows and growing, and a dashboard that re-derives percentiles on every
poll is a dashboard that gets turned off.

Two tiers, so "complete" stayed shippable (§2 of the plan):

* **Tier 1 — Postgres-native.** Latency, errors, traffic, product, cost,
  denials, SLO burn, data freshness, and Aurora cold-starts (counted from the
  ``db_warming`` 503s the app already logs — an in-app signal, zero AWS calls).
  Always computed.
* **Tier 2 — one CloudWatch pull.** App Runner CPU/memory, Aurora ACU and
  connections, CloudFront cache-hit. Off by default; enabled per deployment
  with ``OPS_CLOUDWATCH_ENABLED=1`` and an IAM read grant on the backend role.
  A slow, throttled, or disabled pull leaves the deck rendering full Tier-1
  telemetry with a ``saturation.available: false`` marker — never an error.

The SLOs are deliberately only two, and honest about what they measure:

* **SLO-A availability** — the share of asks *served* (not error, not
  provider-degraded). Full-answer p95 on this workload is extract-bound
  (~96s in prod), which is an eval-loop lever, not an infrastructure one, so
  it is reported but never an objective.
* **SLO-B responsiveness** — p95 time-to-first-page ≤ 3s, the felt latency.

Error-budget burn is computed against the 28-day window regardless of which
window the caller asked for, because a budget is a property of the objective's
period, not of whatever the dashboard is currently showing.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from .config import settings
from .db import admin_ro_connection, rls_connection

log = logging.getLogger("uvicorn.error")

# The windows the deck offers. Keys are the ops_rollup primary key, values the
# Postgres interval they mean.
WINDOWS: dict[str, str] = {"24h": "24 hours", "7d": "7 days", "28d": "28 days"}
DEFAULT_WINDOW = "24h"

# The objectives (W2). Kept here rather than in config so the numbers the deck
# grades against are reviewable in a diff, not settable by an env var.
SLO_AVAILABILITY_TARGET = 0.99  # share of asks served
SLO_TTFP_P95_MS = 3_000  # time to first page
SLO_BUDGET_WINDOW = "28 days"

# The rollup is refreshed on demand (an explicit refresh, or the read path
# noticing this staleness). Past this the summary endpoint kicks a background
# refresh; it still serves the stale row immediately and shows refreshed_at, so
# the lag is visible rather than hidden.
STALE_AFTER_S = 300


def _f(value: Any) -> float | None:
    """A jsonb-safe float — Decimal/None-tolerant, so numeric columns survive."""
    if value is None:
        return None
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _rate(numerator: Any, denominator: Any) -> float | None:
    n, d = _f(numerator), _f(denominator)
    if n is None or not d:
        return None
    return round(n / d, 6)


async def _latency(conn: AsyncConnection, interval: str) -> dict[str, Any]:
    """Full-answer and time-to-first-page percentiles over the agent's asks."""
    row = (
        await conn.execute(
            text(
                "SELECT count(*) AS asks, "
                "  percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS answer_p50, "
                "  percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS answer_p95, "
                "  percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms) AS answer_p99, "
                "  percentile_cont(0.5) WITHIN GROUP (ORDER BY ttfp_ms) AS ttfp_p50, "
                "  percentile_cont(0.95) WITHIN GROUP (ORDER BY ttfp_ms) AS ttfp_p95, "
                "  percentile_cont(0.99) WITHIN GROUP (ORDER BY ttfp_ms) AS ttfp_p99 "
                "FROM app.query_runs "
                f"WHERE source = 'agent' AND created_at >= now() - interval '{interval}'"
            )
        )
    ).one()
    return {
        "asks": int(row.asks or 0),
        "answer_p50_ms": _f(row.answer_p50),
        "answer_p95_ms": _f(row.answer_p95),
        "answer_p99_ms": _f(row.answer_p99),
        "ttfp_p50_ms": _f(row.ttfp_p50),
        "ttfp_p95_ms": _f(row.ttfp_p95),
        "ttfp_p99_ms": _f(row.ttfp_p99),
    }


async def _errors(conn: AsyncConnection, interval: str) -> dict[str, Any]:
    """Error / degraded / no-answer rates, plus a per-source error breakdown.

    ``no_answer`` is the agent honestly reporting the marts can't answer the
    question (``report.no_answer``), which is a *product* signal, not a failure
    — it is counted separately from errors, never folded into them.
    """
    row = (
        await conn.execute(
            text(
                "SELECT count(*) AS runs, "
                "  count(*) FILTER (WHERE qr.status = 'error') AS errors, "
                "  count(*) FILTER (WHERE qr.status = 'degraded' OR qr.degraded) AS degraded, "
                "  count(*) FILTER (WHERE qr.source = 'agent') AS asks, "
                "  count(*) FILTER (WHERE m.report->>'no_answer' = 'true') AS no_answer "
                "FROM app.query_runs qr "
                "LEFT JOIN app.messages m ON m.id = qr.message_id "
                f"WHERE qr.created_at >= now() - interval '{interval}'"
            )
        )
    ).one()
    by_source = {
        r.source or "unknown": int(r.n)
        for r in await conn.execute(
            text(
                "SELECT source, count(*) AS n FROM app.query_runs "
                f"WHERE created_at >= now() - interval '{interval}' "
                "  AND (status = 'error' OR status = 'degraded' OR degraded) "
                "GROUP BY source"
            )
        )
    }
    runs = int(row.runs or 0)
    asks = int(row.asks or 0)
    return {
        "runs": runs,
        "errors": int(row.errors or 0),
        "degraded": int(row.degraded or 0),
        "no_answer": int(row.no_answer or 0),
        "error_rate": _rate(row.errors, runs),
        "degraded_rate": _rate(row.degraded, runs),
        "no_answer_rate": _rate(row.no_answer, asks),
        "by_source": by_source,
    }


async def _traffic(conn: AsyncConnection, interval: str) -> dict[str, Any]:
    """Asks, active users, and the surface mix (chat / explore / sql editor)."""
    row = (
        await conn.execute(
            text(
                "SELECT count(*) AS runs, count(DISTINCT user_id) AS active_users, "
                "  count(*) FILTER (WHERE source = 'agent') AS asks "
                "FROM app.query_runs "
                f"WHERE created_at >= now() - interval '{interval}'"
            )
        )
    ).one()
    mix = {
        r.source or "unknown": int(r.n)
        for r in await conn.execute(
            text(
                "SELECT source, count(*) AS n FROM app.query_runs "
                f"WHERE created_at >= now() - interval '{interval}' GROUP BY source"
            )
        )
    }
    active = int(row.active_users or 0)
    return {
        "runs": int(row.runs or 0),
        "asks": int(row.asks or 0),
        "active_users": active,
        "asks_per_user": _rate(row.asks, active),
        "by_source": mix,
    }


async def _cost(conn: AsyncConnection, interval: str) -> dict[str, Any]:
    """Priced spend and the cache-hit ratio that makes it ~1/6 of nominal.

    Rows written before W2 carry no ``cost_usd``; they are counted in ``priced``
    so the deck can say "12 of 512 asks priced" instead of implying the total is
    complete.
    """
    row = (
        await conn.execute(
            text(
                "SELECT coalesce(sum(cost_usd), 0) AS total_usd, "
                "  count(*) FILTER (WHERE cost_usd IS NOT NULL) AS priced, "
                "  coalesce(sum(input_tokens), 0) AS input_tokens, "
                "  coalesce(sum(output_tokens), 0) AS output_tokens, "
                "  coalesce(sum(cache_read_tokens), 0) AS cache_read_tokens, "
                "  coalesce(sum(cache_write_tokens), 0) AS cache_write_tokens "
                "FROM app.query_runs "
                f"WHERE source = 'agent' AND created_at >= now() - interval '{interval}'"
            )
        )
    ).one()
    priced = int(row.priced or 0)
    return {
        "total_usd": _f(row.total_usd),
        "priced_asks": priced,
        "per_answer_usd": _rate(row.total_usd, priced),
        "input_tokens": int(row.input_tokens or 0),
        "output_tokens": int(row.output_tokens or 0),
        "cache_read_tokens": int(row.cache_read_tokens or 0),
        "cache_write_tokens": int(row.cache_write_tokens or 0),
        "cache_hit_ratio": _rate(row.cache_read_tokens, row.input_tokens),
        "budget_usd": _f(settings.ops_monthly_budget_usd),
    }


async def _product(conn: AsyncConnection, interval: str) -> dict[str, Any]:
    """Thumbs from app.answer_feedback — useful, not just up."""
    row = (
        await conn.execute(
            text(
                "SELECT count(*) FILTER (WHERE rating = 1) AS up, "
                "  count(*) FILTER (WHERE rating = -1) AS down, count(*) AS total "
                "FROM app.answer_feedback "
                f"WHERE created_at >= now() - interval '{interval}'"
            )
        )
    ).one()
    return {
        "thumbs_up": int(row.up or 0),
        "thumbs_down": int(row.down or 0),
        "thumbs_up_rate": _rate(row.up, row.total),
    }


async def _security(conn: AsyncConnection, interval: str) -> dict[str, Any]:
    """Denials, auth failures and cap hits, counted from the event stream (W3).

    ``security_denied`` is emitted wherever a guard actually refuses — the SQL
    guard rejecting generated or user-typed SQL. A zero-row RLS result is
    deliberately *not* counted: "no rows matched" and "you may not see these
    rows" are indistinguishable at the query layer, and inflating the denial
    counter with empty result sets would make the number useless.
    """
    counts = {
        r.event_type: int(r.n)
        for r in await conn.execute(
            text(
                "SELECT event_type, count(*) AS n FROM app.events "
                f"WHERE created_at >= now() - interval '{interval}' "
                "  AND event_type IN ('security_denied', 'login_failure', 'llm_cap_reached') "
                "GROUP BY event_type"
            )
        )
    }
    latest = (
        (
            await conn.execute(
                text(
                    "SELECT created_at, kind, total, passed, by_category, report_url "
                    "FROM app.security_runs ORDER BY created_at DESC LIMIT 1"
                )
            )
        )
        .mappings()
        .first()
    )
    return {
        "denials": counts.get("security_denied", 0),
        "auth_failures": counts.get("login_failure", 0),
        "cap_hits": counts.get("llm_cap_reached", 0),
        "latest_run": _latest_security_run(latest),
    }


def _latest_security_run(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    total = int(row["total"] or 0)
    return {
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "kind": row["kind"],
        "total": total,
        "passed": int(row["passed"] or 0),
        "pass_rate": _rate(row["passed"], total),
        "by_category": row["by_category"] or {},
        "report_url": row["report_url"],
    }


async def _reliability(conn: AsyncConnection, interval: str) -> dict[str, Any]:
    """Retry/degrade behaviour (W1) plus Aurora cold-starts, counted in-app.

    Every ``db_warming`` 503 the app returns is a request that landed while
    Aurora Serverless was resuming from auto-pause (s29). Counting the *events*
    the frontend already reports gives a cold-start signal with no CloudWatch
    call at all — Tier 1 by construction.
    """
    row = (
        await conn.execute(
            text(
                "SELECT avg(attempts) AS attempts_mean, "
                "  count(*) FILTER (WHERE attempts > 1) AS retried "
                "FROM app.query_runs "
                f"WHERE source = 'agent' AND created_at >= now() - interval '{interval}'"
            )
        )
    ).one()
    waking = (
        await conn.execute(
            text(
                "SELECT count(*) AS n FROM app.events "
                f"WHERE created_at >= now() - interval '{interval}' "
                "  AND event_type = 'db_warming'"
            )
        )
    ).scalar() or 0
    load = (
        (
            await conn.execute(
                text(
                    "SELECT created_at, scenario, vus, rps, p50_ms, p95_ms, p99_ms, error_rate "
                    "FROM app.load_tests ORDER BY created_at DESC LIMIT 1"
                )
            )
        )
        .mappings()
        .first()
    )
    return {
        "attempts_mean": _f(row.attempts_mean),
        "retried": int(row.retried or 0),
        "db_cold_starts": int(waking),
        "latest_load_test": (
            {
                "created_at": load["created_at"].isoformat() if load["created_at"] else None,
                "scenario": load["scenario"],
                "vus": load["vus"],
                "rps": _f(load["rps"]),
                "p50_ms": load["p50_ms"],
                "p95_ms": load["p95_ms"],
                "p99_ms": load["p99_ms"],
                "error_rate": _f(load["error_rate"]),
            }
            if load is not None
            else None
        ),
    }


async def _slo(conn: AsyncConnection) -> dict[str, Any]:
    """The two objectives plus error-budget burn, always over 28 days.

    A budget belongs to the objective's period, not to whichever window the
    dashboard happens to be showing — computing it per-window would let a quiet
    24h make a blown month look healthy.
    """
    row = (
        await conn.execute(
            text(
                "SELECT count(*) AS asks, "
                "  count(*) FILTER (WHERE status = 'success' AND NOT degraded) AS served, "
                "  count(*) FILTER (WHERE ttfp_ms IS NOT NULL) AS measured, "
                "  count(*) FILTER (WHERE ttfp_ms IS NOT NULL AND ttfp_ms <= :ttfp) AS fast, "
                "  percentile_cont(0.95) WITHIN GROUP (ORDER BY ttfp_ms) AS ttfp_p95 "
                "FROM app.query_runs WHERE source = 'agent' "
                f"  AND created_at >= now() - interval '{SLO_BUDGET_WINDOW}'"
            ),
            {"ttfp": SLO_TTFP_P95_MS},
        )
    ).one()
    asks = int(row.asks or 0)
    attained = _rate(row.served, asks)
    # Burn: how much of the allowed unavailability has been spent. 0% = untouched
    # budget, 100% = exactly at the objective, >100% = objective missed.
    allowed = 1.0 - SLO_AVAILABILITY_TARGET
    burn = None
    if attained is not None and allowed > 0:
        burn = round(min(max((1.0 - attained) / allowed, 0.0), 9.99), 4)
    ttfp_p95 = _f(row.ttfp_p95)
    return {
        "window": SLO_BUDGET_WINDOW,
        "availability": {
            "target": SLO_AVAILABILITY_TARGET,
            "attained": attained,
            "asks": asks,
            "served": int(row.served or 0),
            "error_budget_burn": burn,
            "state": _slo_state(attained, SLO_AVAILABILITY_TARGET, higher_is_better=True),
        },
        "responsiveness": {
            "target_ms": SLO_TTFP_P95_MS,
            "attained_ms": ttfp_p95,
            "measured": int(row.measured or 0),
            "fast": int(row.fast or 0),
            "state": _slo_state(ttfp_p95, SLO_TTFP_P95_MS, higher_is_better=False),
        },
    }


def _slo_state(value: float | None, target: float, *, higher_is_better: bool) -> str:
    """A lamp state for an objective: no data → off, met → on, near → warn, else bad.

    "Near" is within 10% of the objective on the wrong side — the point at which
    a lamp should draw attention before the objective is actually missed.
    """
    if value is None:
        return "off"
    if higher_is_better:
        if value >= target:
            return "on"
        return "warn" if value >= target * 0.99 else "bad"
    if value <= target:
        return "on"
    return "warn" if value <= target * 1.1 else "bad"


async def _freshness(conn: AsyncConnection) -> dict[str, Any]:
    """Marts age + dbt pass from app.pipeline_runs (W2).

    This is the metric whose absence took Explore down: prod marts froze for 12
    days while the app kept deploying. Age is measured from the pipeline's own
    write, so no AWS call is involved.
    """
    row = (
        (
            await conn.execute(
                text(
                    "SELECT created_at, status, duration_s, marts_refreshed_at, dbt_pass, "
                    "  dbt_total, row_counts, source, EXTRACT(EPOCH FROM "
                    "    (now() - coalesce(marts_refreshed_at, created_at))) AS age_s "
                    "FROM app.pipeline_runs WHERE status <> 'running' "
                    "ORDER BY created_at DESC LIMIT 1"
                )
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return {"available": False}
    age_s = _f(row["age_s"])
    return {
        "available": True,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "status": row["status"],
        "duration_s": row["duration_s"],
        "age_s": age_s,
        "dbt_pass": row["dbt_pass"],
        "dbt_total": row["dbt_total"],
        "row_counts": row["row_counts"] or {},
        "source": row["source"],
        # 24h is the staleness line: the pipeline runs on every deploy, so a
        # day-old mart means no deploy and no manual run — the exact prod state
        # that broke Explore.
        "state": "off" if age_s is None else ("on" if age_s <= 86_400 else "bad"),
    }


async def _deploys(conn: AsyncConnection) -> list[dict[str, Any]]:
    """The last few deploys for the timeline panel (W4)."""
    return [
        {
            "id": str(r["id"]),
            "started_at": r["started_at"].isoformat() if r["started_at"] else None,
            "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None,
            "git_sha": (r["git_sha"] or "")[:7],
            "actor": r["actor"],
            "status": r["status"],
            "duration_s": r["duration_s"],
            "smoke": r["smoke"] or {},
        }
        for r in (
            await conn.execute(
                text(
                    "SELECT id, started_at, finished_at, git_sha, actor, status, duration_s, "
                    "  smoke FROM app.deploy_events ORDER BY started_at DESC LIMIT 8"
                )
            )
        ).mappings()
    ]


async def _judge(conn: AsyncConnection, interval: str) -> dict[str, Any]:
    """Online judge sampling — advisory answer-quality drift (W4)."""
    row = (
        await conn.execute(
            text(
                "SELECT count(*) AS sampled, avg(insight_score) AS mean_score, "
                "  max(created_at) AS latest_at "
                "FROM app.judge_samples "
                f"WHERE created_at >= now() - interval '{interval}'"
            )
        )
    ).one()
    return {
        "sampled": int(row.sampled or 0),
        "insight_mean": _f(row.mean_score),
        "latest_at": row.latest_at.isoformat() if row.latest_at else None,
    }


async def _saturation() -> dict[str, Any]:
    """Tier 2: one CloudWatch GetMetricData pull, off the request path.

    Everything here is a *nice to have* on a deck whose job is to stay up, so
    every failure mode — boto3 absent, no IAM grant, throttled, slow — returns
    ``available: false`` and the deck renders Tier 1 alone.
    """
    if not settings.ops_cloudwatch_enabled:
        return {"available": False, "reason": "disabled"}
    try:
        from .ops_cloudwatch import fetch_saturation

        return await fetch_saturation()
    except Exception as exc:  # noqa: BLE001 — a metrics pull must never break the deck
        log.warning("ops saturation pull skipped: %s", exc)
        return {"available": False, "reason": str(exc)[:200]}


async def compute_window(conn: AsyncConnection, window_key: str) -> dict[str, Any]:
    """Aggregate every Tier-1 metric (plus Tier 2 when enabled) for one window."""
    interval = WINDOWS.get(window_key, WINDOWS[DEFAULT_WINDOW])
    return {
        "window": window_key,
        "interval": interval,
        "latency": await _latency(conn, interval),
        "errors": await _errors(conn, interval),
        "traffic": await _traffic(conn, interval),
        "cost": await _cost(conn, interval),
        "product": await _product(conn, interval),
        "security": await _security(conn, interval),
        "reliability": await _reliability(conn, interval),
        "judge": await _judge(conn, interval),
        "slo": await _slo(conn),
        "freshness": await _freshness(conn),
        "deploys": await _deploys(conn),
        "saturation": await _saturation(),
    }


async def _upsert(conn: AsyncConnection, window_key: str, metrics: dict[str, Any]) -> None:
    await conn.execute(
        text(
            "INSERT INTO app.ops_rollup (window_key, refreshed_at, metrics) "
            "VALUES (:key, now(), CAST(:metrics AS jsonb)) "
            "ON CONFLICT (window_key) DO UPDATE "
            "SET refreshed_at = now(), metrics = EXCLUDED.metrics"
        ),
        {"key": window_key, "metrics": json.dumps(metrics)},
    )


async def refresh_window(window_key: str) -> dict[str, Any]:
    """Recompute one window and upsert it into app.ops_rollup.

    Two connections, on purpose:

    * **read** through ``admin_ro`` (BYPASSRLS, SELECT-only) because this is an
      aggregate over every user, and no single user's RLS context can see it. An
      empty context sees *zero* rows, so getting this wrong doesn't error — it
      quietly writes a rollup of zeros and blanks the deck.
    * **write** through the normal ``app_user`` connection, since ``admin_ro``
      cannot write and should not be able to.

    Opens its own connections rather than taking one, so every caller — the
    admin endpoint, the background stale-trigger, the machine-token scheduler
    hook — gets identical behaviour instead of each having to remember which
    authority to pass.
    """
    async with admin_ro_connection() as read_conn:
        metrics = await compute_window(read_conn, window_key)
    async with rls_connection(None) as write_conn:
        # app.ops_rollup has no RLS (admin/CI-curated, like the eval tables), so
        # an empty context is fine for the write.
        await _upsert(write_conn, window_key, metrics)
    return metrics


async def refresh_all() -> list[str]:
    """Recompute every window. The scheduled/adhoc refresh entry point."""
    refreshed: list[str] = []
    for key in WINDOWS:
        await refresh_window(key)
        refreshed.append(key)
    return refreshed
