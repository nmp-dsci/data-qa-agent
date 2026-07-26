"""Tier-2 infra saturation: one CloudWatch GetMetricData pull (s32 W2).

Saturation is the fourth Golden Signal and the one thing the app genuinely
cannot see from inside Postgres: App Runner CPU/memory, Aurora ACU and
connection counts, CloudFront cache-hit ratio. This module is the *only* place
the backend talks to an AWS API, and it does so exactly once per rollup refresh
— never on a request — so a throttled or slow CloudWatch never shows up as deck
latency.

Everything is best-effort by construction. ``boto3`` is an optional dependency,
the IAM grant is a separate Terraform change, and the whole call is wrapped by
the caller. If any of that is missing the deck renders Tier 1 alone with a
"saturation unavailable" marker, which is the honest state — not an error.

One ``GetMetricData`` call carries every query, so the cost is a single request
per refresh regardless of how many metrics the deck grows.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from .config import settings

# 15 minutes of 5-minute datapoints: enough to read "now" without a single
# scrape gap blanking the panel, cheap enough to stay one request.
_LOOKBACK = timedelta(minutes=15)
_PERIOD_S = 300


def _metric_queries() -> list[dict[str, Any]]:
    """The GetMetricData query set. Ids are the keys the deck reads back."""
    backend = settings.ops_apprunner_backend_service
    agent = settings.ops_apprunner_agent_service
    cluster = settings.ops_aurora_cluster_id
    distribution = settings.ops_cloudfront_distribution_id

    queries: list[dict[str, Any]] = []

    def add(mid: str, namespace: str, name: str, dims: dict[str, str], stat: str) -> None:
        queries.append(
            {
                "Id": mid,
                "MetricStat": {
                    "Metric": {
                        "Namespace": namespace,
                        "MetricName": name,
                        "Dimensions": [{"Name": k, "Value": v} for k, v in dims.items()],
                    },
                    "Period": _PERIOD_S,
                    "Stat": stat,
                },
                "ReturnData": True,
            }
        )

    for mid, service in (("backend", backend), ("agent", agent)):
        if not service:
            continue
        add(f"{mid}_cpu", "AWS/AppRunner", "CPUUtilization", {"ServiceName": service}, "Average")
        add(f"{mid}_mem", "AWS/AppRunner", "MemoryUtilization", {"ServiceName": service}, "Average")
        add(
            f"{mid}_instances",
            "AWS/AppRunner",
            "ActiveInstances",
            {"ServiceName": service},
            "Maximum",
        )
    if cluster:
        add(
            "aurora_acu",
            "AWS/RDS",
            "ServerlessDatabaseCapacity",
            {"DBClusterIdentifier": cluster},
            "Average",
        )
        add(
            "aurora_conns",
            "AWS/RDS",
            "DatabaseConnections",
            {"DBClusterIdentifier": cluster},
            "Maximum",
        )
    if distribution:
        # CloudFront metrics only exist in us-east-1 — handled by the caller's
        # region override below.
        add(
            "cdn_cache_hit",
            "AWS/CloudFront",
            "CacheHitRate",
            {"DistributionId": distribution, "Region": "Global"},
            "Average",
        )
    return queries


def _latest(results: list[dict[str, Any]]) -> dict[str, float | None]:
    """The newest datapoint per metric id (GetMetricData returns descending)."""
    out: dict[str, float | None] = {}
    for series in results:
        values = series.get("Values") or []
        out[series.get("Id", "")] = round(float(values[0]), 3) if values else None
    return out


def _fetch_sync() -> dict[str, Any]:
    import boto3  # imported here so the module stays importable without it

    queries = _metric_queries()
    if not queries:
        return {"available": False, "reason": "no service identifiers configured"}

    end = datetime.now(UTC)
    start = end - _LOOKBACK
    region = settings.ops_cloudwatch_region or None

    # CloudFront publishes only to us-east-1, so it needs its own client. Two
    # clients, still one call each at most — and the CDN half is skipped
    # entirely when no distribution id is configured.
    cdn_queries = [q for q in queries if q["Id"].startswith("cdn_")]
    main_queries = [q for q in queries if not q["Id"].startswith("cdn_")]

    latest: dict[str, float | None] = {}
    if main_queries:
        client = boto3.client("cloudwatch", region_name=region)
        latest.update(
            _latest(
                client.get_metric_data(
                    MetricDataQueries=main_queries,
                    StartTime=start,
                    EndTime=end,
                    ScanBy="TimestampDescending",
                )["MetricDataResults"]
            )
        )
    if cdn_queries:
        cdn = boto3.client("cloudwatch", region_name="us-east-1")
        latest.update(
            _latest(
                cdn.get_metric_data(
                    MetricDataQueries=cdn_queries,
                    StartTime=start,
                    EndTime=end,
                    ScanBy="TimestampDescending",
                )["MetricDataResults"]
            )
        )

    return {
        "available": True,
        "fetched_at": end.isoformat(),
        "backend": {
            "cpu_pct": latest.get("backend_cpu"),
            "mem_pct": latest.get("backend_mem"),
            "instances": latest.get("backend_instances"),
        },
        "agent": {
            "cpu_pct": latest.get("agent_cpu"),
            "mem_pct": latest.get("agent_mem"),
            "instances": latest.get("agent_instances"),
        },
        "aurora": {
            "acu": latest.get("aurora_acu"),
            "connections": latest.get("aurora_conns"),
        },
        "cdn": {"cache_hit_rate": latest.get("cdn_cache_hit")},
        # The concurrency ceiling is a Terraform constant (apprunner.tf
        # max_concurrency), not a metric — carried here so the deck can show
        # "6 / 100" rather than a bare number with no scale.
        "limits": {"max_concurrency": settings.ops_apprunner_max_concurrency},
    }


async def fetch_saturation() -> dict[str, Any]:
    """Pull the saturation metrics without blocking the event loop.

    boto3 is synchronous, so it runs in a worker thread under a hard timeout —
    a hung AWS API must not hold the refresh open.
    """
    return await asyncio.wait_for(
        asyncio.to_thread(_fetch_sync), timeout=settings.ops_cloudwatch_timeout_s
    )
