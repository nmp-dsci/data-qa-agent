#!/usr/bin/env python3
"""Post an operational outcome to the ops deck (s32).

One writer for every "this ran, here is what happened" record: a k6 load test, a
promptfoo red-team pack, a deploy, a pipeline run, or a rollup refresh. They all
go through ``POST /ops/ingest/*`` on backend-api rather than straight to
Postgres, for a reason worth stating: **the writers can't reach the database.**
Aurora's security group admits the one-shot ECS jobs, App Runner's egress
ranges, and operator CIDRs — not GitHub Actions runners and not a developer
laptop without an allowlisted IP. backend-api can, so it owns the write, behind
a machine token (``OPS_INGEST_TOKEN``) that is empty by default, which closes the
endpoint entirely rather than leaving it open.

Usage::

    # k6 → app.load_tests (reads k6's --summary-export JSON)
    python scripts/ops_ingest.py load-test --k6-summary load/summary.json \\
        --scenario chat --vus 3 --duration-s 60

    # promptfoo → app.security_runs
    python scripts/ops_ingest.py security-run --promptfoo-json security/out.json

    # deploy start / finish → app.deploy_events
    python scripts/ops_ingest.py deploy --sha "$GITHUB_SHA" --actor "$GITHUB_ACTOR"
    python scripts/ops_ingest.py deploy --sha "$GITHUB_SHA" --status deployed \\
        --smoke-passed 8 --smoke-total 8

    # recompute the deck's windows (a scheduler's hook)
    python scripts/ops_ingest.py rollup

Config comes from the environment so the same command works locally, in CI and
against prod: ``OPS_API_URL`` (default the local backend) and
``OPS_INGEST_TOKEN``. Deliberately dependency-free (urllib, not requests) so it
runs on a bare GitHub runner with no install step.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_API = os.environ.get("OPS_API_URL", "http://localhost:8000")


def _post(path: str, body: dict[str, Any], *, api: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{api.rstrip('/')}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Ops-Token": token},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else {}


# ---------------------------------------------------------------------------
# k6
# ---------------------------------------------------------------------------


def _k6_metric(summary: dict[str, Any], name: str) -> dict[str, Any]:
    metrics = summary.get("metrics") or {}
    entry = metrics.get(name) or {}
    # k6 has moved this shape around across versions: older exports put the
    # percentiles at the top level, newer ones nest them under "values".
    return entry.get("values", entry)


def _pick(values: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if values.get(key) is not None:
            try:
                return float(values[key])
            except (TypeError, ValueError):
                continue
    return None


def load_test_body(args: argparse.Namespace) -> dict[str, Any]:
    """Reduce a k6 summary export to the row app.load_tests holds.

    The latency metric is chosen by scenario, not averaged across both: `chat`
    reports the /ask trend and `browse` the read trend, because mixing a
    90-second report into the same p95 as a 40ms /me makes both numbers lies.
    """
    summary: dict[str, Any] = {}
    if args.k6_summary:
        summary = json.loads(Path(args.k6_summary).read_text())

    trend_name = "ask_duration" if args.scenario == "chat" else "browse_duration"
    trend = _k6_metric(summary, trend_name) or _k6_metric(summary, "http_req_duration")
    reqs = _k6_metric(summary, "http_reqs")
    failed = _k6_metric(summary, "http_req_failed")

    def ms(value: float | None) -> int | None:
        return None if value is None else int(round(value))

    return {
        "scenario": args.scenario,
        "vus": args.vus,
        "duration_s": args.duration_s,
        "rps": _pick(reqs, "rate"),
        "p50_ms": ms(_pick(trend, "med", "p(50)")),
        "p95_ms": ms(_pick(trend, "p(95)")),
        "p99_ms": ms(_pick(trend, "p(99)")),
        "error_rate": _pick(failed, "rate", "value"),
        "git_sha": args.sha,
        "notes": args.notes,
    }


# ---------------------------------------------------------------------------
# promptfoo
# ---------------------------------------------------------------------------


def security_run_body(args: argparse.Namespace) -> dict[str, Any]:
    """Reduce a promptfoo JSON report to per-category pass rates.

    Category comes from each test's ``metadata.category`` (set in
    ``security/promptfoo/redteam.yaml``), so the deck's bars are labelled with
    attack classes — "prompt injection", "RLS bypass" — rather than test indexes.
    An unlabelled test lands in "uncategorised" rather than being dropped, so a
    forgotten label shows up as a gap instead of silently shrinking the total.
    """
    if not args.promptfoo_json:
        return {
            "kind": args.kind,
            "pack_sha": args.pack_sha,
            "total": args.total,
            "passed": args.passed,
            "by_category": json.loads(args.by_category) if args.by_category else {},
            "report_url": args.report_url,
        }

    report = json.loads(Path(args.promptfoo_json).read_text())
    results = report.get("results", {}).get("results") or report.get("results") or []
    by_category: dict[str, dict[str, int]] = {}
    total = passed = 0
    for result in results:
        if not isinstance(result, dict):
            continue
        total += 1
        ok = bool(result.get("success"))
        passed += 1 if ok else 0
        meta = (result.get("testCase") or {}).get("metadata") or result.get("metadata") or {}
        category = str(meta.get("category") or "uncategorised")
        bucket = by_category.setdefault(category, {"total": 0, "passed": 0})
        bucket["total"] += 1
        bucket["passed"] += 1 if ok else 0

    return {
        "kind": args.kind,
        "pack_sha": args.pack_sha,
        "total": total,
        "passed": passed,
        "by_category": by_category,
        "report_url": args.report_url,
    }


# ---------------------------------------------------------------------------
# deploy / pipeline
# ---------------------------------------------------------------------------


def deploy_body(args: argparse.Namespace) -> dict[str, Any]:
    smoke: dict[str, Any] = {}
    if args.smoke_total is not None:
        smoke = {"passed": args.smoke_passed or 0, "total": args.smoke_total}
    return {
        "git_sha": args.sha,
        "actor": args.actor,
        "status": args.status,
        "smoke": smoke,
        "duration_s": args.duration_s,
        "notes": args.notes,
    }


def pipeline_body(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "status": args.status,
        "duration_s": args.duration_s,
        "marts_refreshed_at": args.marts_refreshed_at,
        "dbt_pass": args.dbt_pass,
        "dbt_total": args.dbt_total,
        "row_counts": json.loads(args.row_counts) if args.row_counts else {},
        "git_sha": args.sha,
        "source": args.source,
        "notes": args.notes,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default=DEFAULT_API, help="backend-api base URL")
    parser.add_argument(
        "--token",
        default=os.environ.get("OPS_INGEST_TOKEN", ""),
        help="machine token (default $OPS_INGEST_TOKEN)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    load = sub.add_parser("load-test", help="record a k6 run")
    load.add_argument("--k6-summary", help="path to k6 --summary-export JSON")
    load.add_argument("--scenario", default="browse")
    load.add_argument("--vus", type=int)
    load.add_argument("--duration-s", type=int)
    load.add_argument("--sha", default=os.environ.get("GITHUB_SHA", ""))
    load.add_argument("--notes")

    security = sub.add_parser("security-run", help="record a red-team / injection pack")
    security.add_argument("--promptfoo-json", help="path to a promptfoo JSON report")
    security.add_argument("--kind", default="redteam", choices=["redteam", "injection"])
    security.add_argument("--pack-sha")
    security.add_argument("--total", type=int, default=0)
    security.add_argument("--passed", type=int, default=0)
    security.add_argument("--by-category", help="JSON object, when not reading a report")
    security.add_argument("--report-url")

    deploy = sub.add_parser("deploy", help="start or finish a deploy record")
    deploy.add_argument("--sha", default=os.environ.get("GITHUB_SHA", ""), required=False)
    deploy.add_argument("--actor", default=os.environ.get("GITHUB_ACTOR", ""))
    deploy.add_argument(
        "--status", default="running", choices=["running", "deployed", "rolled_back", "failed"]
    )
    deploy.add_argument("--smoke-passed", type=int)
    deploy.add_argument("--smoke-total", type=int)
    deploy.add_argument("--duration-s", type=int)
    deploy.add_argument("--notes")

    pipeline = sub.add_parser("pipeline-run", help="record a pipeline run")
    pipeline.add_argument("--status", default="success", choices=["running", "success", "failed"])
    pipeline.add_argument("--duration-s", type=int)
    pipeline.add_argument("--marts-refreshed-at")
    pipeline.add_argument("--dbt-pass", type=int)
    pipeline.add_argument("--dbt-total", type=int)
    pipeline.add_argument("--row-counts", help="JSON object of table -> row count")
    pipeline.add_argument("--sha", default=os.environ.get("GITHUB_SHA", ""))
    pipeline.add_argument("--source")
    pipeline.add_argument("--notes")

    sub.add_parser("rollup", help="recompute every ops_rollup window")
    return parser


_ROUTES = {
    "load-test": ("/ops/ingest/load-test", load_test_body),
    "security-run": ("/ops/ingest/security-run", security_run_body),
    "deploy": ("/ops/ingest/deploy", deploy_body),
    "pipeline-run": ("/ops/ingest/pipeline-run", pipeline_body),
    "rollup": ("/ops/ingest/rollup", lambda _args: {}),
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.token:
        # Not an error worth failing a deploy over: telemetry is not the deploy.
        print("[ops-ingest] no OPS_INGEST_TOKEN set — skipping", file=sys.stderr)
        return 0
    path, builder = _ROUTES[args.command]
    body = builder(args)
    try:
        result = _post(path, body, api=args.api, token=args.token)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        # Same reasoning: a telemetry write must never be the thing that fails a
        # deploy or a load run. Loud on stderr, exit 0.
        print(f"[ops-ingest] {args.command} failed: {exc}", file=sys.stderr)
        return 0
    print(f"[ops-ingest] {args.command}: {json.dumps(result)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
