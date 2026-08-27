"""s41: the worker-scaling sweep — W x S grid, measured against ceil(N/W) x S.

Drives the whole experiment in one command (stdlib only; docker + the SSE
prober do the work):

    python3 scripts/wsweep.py                 # 9 cells: W in 1/3/5 x S in 10/30/60
    python3 scripts/wsweep.py --bonus-only    # just the WORKER_CONCURRENCY=4 cell
    python3 scripts/wsweep.py --cells W1-S10,W5-S60

Per cell it: restacks the queue stack with the cell's knobs (same build, env
only), waits until every worker replica is consuming, fires a burst of N
one-shot users through scripts/loadgen_sse.py, snapshots Prometheus over the
cell's window (queue-wait p95, service p95, worker CPU/memory via cAdvisor),
and appends the row to out/wsweep/summary.json. Fairness rules from the s41
plan: QUEUE_MAX_DEPTH=32 (capacity cells must not shed) and JOB_DEADLINE_S=1200
(W1-S60's last job legitimately waits 840s).
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "wsweep"
BASE_URL = "http://localhost:8010"
PROM_URL = "http://localhost:9090"
USERS = 15

GRID = [
    {"cell": f"W{w}-S{s}", "workers": w, "stub_s": s, "conc": 1}
    for w in (1, 3, 5)
    for s in (10, 30, 60)
]
BONUS = {"cell": "W1-S30-C4", "workers": 1, "stub_s": 30, "conc": 4}


def sh(cmd: list[str], *, env: dict[str, str] | None = None, timeout: int = 300) -> str:
    import os

    merged = {**os.environ, **(env or {})}
    proc = subprocess.run(
        cmd, cwd=ROOT, env=merged, capture_output=True, text=True, timeout=timeout
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed:\n{proc.stderr[-2000:]}")
    return proc.stdout


def restack(cell: dict[str, Any]) -> None:
    env = {
        "COMPOSE_PROFILES": "queue,obs",
        "QUEUE_MODE": "on",
        "LLM_STUB": "1",
        "STUB_LATENCY_S": str(cell["stub_s"]),
        "QUEUE_MAX_DEPTH": "32",
        "JOB_DEADLINE_S": "1200",
        "WORKER_CONCURRENCY": str(cell["conc"]),
    }
    sh(
        [
            "docker",
            "compose",
            "up",
            "-d",
            "--no-deps",
            "--scale",
            f"agent-worker={cell['workers']}",
            "agent-worker",
            "backend-api",
            "data-agent",
        ],
        env=env,
    )


def wait_ready(workers: int, timeout_s: int = 240) -> None:
    """Backend healthy + every (freshly recreated) replica printed 'consuming'."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE_URL}/health", timeout=3) as resp:
                healthy = resp.status == 200
        except OSError:
            healthy = False
        consuming = 0
        try:
            logs = sh(["docker", "compose", "logs", "agent-worker"], timeout=30)
            consuming = logs.count("] consuming ")
        except RuntimeError:
            pass
        depth = -1
        try:
            depth = int(
                sh(
                    ["docker", "compose", "exec", "-T", "redis", "redis-cli", "xlen", "agent:jobs"],
                    timeout=30,
                ).strip()
            )
        except (RuntimeError, ValueError):
            pass
        if healthy and consuming >= workers and depth == 0:
            return
        time.sleep(3)
    raise RuntimeError(f"stack not ready: want {workers} consuming workers + empty queue")


def prom(query: str) -> float | None:
    url = f"{PROM_URL}/api/v1/query?query={urllib.parse.quote(query)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            payload = json.load(resp)
        result = payload["data"]["result"]
        if not result:
            return None
        value = float(result[0]["value"][1])
        return None if math.isnan(value) else value
    except OSError:
        return None


def snapshot(window_s: int) -> dict[str, float | None]:
    w = f"{window_s}s"
    hq = "histogram_quantile(0.95, sum by (le) (increase({m}_bucket[{w}])))"
    return {
        "wait_p95_s": prom(hq.format(m="dataqa_queue_wait_seconds", w=w)),
        "service_p95_s": prom(hq.format(m="dataqa_worker_service_seconds", w=w)),
        "jobs_ok": prom(f'sum(increase(dataqa_worker_jobs_total{{outcome="ok"}}[{w}]))'),
        "worker_cpu_max_pct": prom(
            f'max_over_time(sum(dataqa_container_cpu_percent{{service="agent-worker"}})[{w}:15s])'
        ),
        "worker_mem_max_mb": prom(
            f'max_over_time(sum(dataqa_container_mem_mb{{service="agent-worker"}})[{w}:15s])'
        ),
        "backend_cpu_max_pct": prom(
            f'max_over_time(sum(dataqa_container_cpu_percent{{service="backend-api"}})[{w}:15s])'
        ),
    }


def burst(cell_name: str, users: int) -> dict[str, Any]:
    out_file = OUT / f"{cell_name}.json"
    sh(
        [
            "uv",
            "run",
            "--with",
            "httpx",
            "python",
            "scripts/loadgen_sse.py",
            "--users",
            str(users),
            "--deadline-s",
            "1500",
            "--json",
            str(out_file),
            "--base-url",
            BASE_URL,
        ],
        timeout=1600,
    )
    return json.loads(out_file.read_text())


def record_load_test(cell: dict[str, Any], row: dict[str, Any]) -> None:
    """Best-effort app.load_tests row via ops_ingest (synthesized k6 shape)."""
    totals = [r["total_s"] * 1000 for r in row["results"] if r.get("total_s")]
    ok = sum(1 for r in row["results"] if r["outcome"] == "ok")
    summary = {
        "metrics": {
            "ask_duration": {
                "med": statistics.median(totals) if totals else None,
                "p(95)": (statistics.quantiles(totals, n=20)[18] if len(totals) >= 2 else totals[0])
                if totals
                else None,
            },
            "http_reqs": {"count": len(row["results"]), "rate": ok / row["makespan_s"]},
            "http_req_failed": {"value": 1 - ok / max(1, len(row["results"]))},
        }
    }
    tmp = OUT / f"{cell['cell']}-k6shape.json"
    tmp.write_text(json.dumps(summary))
    notes = (
        f"wsweep {cell['cell']} workers={cell['workers']} stub_s={cell['stub_s']} "
        f"conc={cell['conc']} users={USERS}"
    )
    subprocess.run(
        [
            sys.executable,
            "scripts/ops_ingest.py",
            "--api",
            BASE_URL,
            "load-test",
            "--k6-summary",
            str(tmp),
            "--scenario",
            "chat",
            "--vus",
            str(USERS),
            "--duration-s",
            str(int(row["makespan_s"])),
            "--notes",
            notes,
        ],
        cwd=ROOT,
    )


def predicted_makespan(cell: dict[str, Any]) -> int:
    slots = cell["workers"] * cell["conc"]
    return math.ceil(USERS / slots) * cell["stub_s"]


def run_cell(cell: dict[str, Any]) -> dict[str, Any]:
    print(
        f"\n=== {cell['cell']}: workers={cell['workers']} stub={cell['stub_s']}s "
        f"conc={cell['conc']} predicted={predicted_makespan(cell)}s ==="
    )
    restack(cell)
    wait_ready(cell["workers"])
    t0 = time.time()
    load = burst(cell["cell"], USERS)
    window = int(time.time() - t0) + 30
    time.sleep(10)  # let the last scrape land before querying the window
    snap = snapshot(window)
    ok = sum(1 for r in load["results"] if r["outcome"] == "ok")
    row = {
        **cell,
        "users": USERS,
        "predicted_makespan_s": predicted_makespan(cell),
        "makespan_s": round(load["makespan_s"], 1),
        "error_pct": round(
            100 * abs(load["makespan_s"] - predicted_makespan(cell)) / predicted_makespan(cell), 1
        ),
        "ok": ok,
        "outcomes": {
            k: sum(1 for r in load["results"] if r["outcome"] == k)
            for k in ("ok", "shed", "error", "timeout")
        },
        "ttfp_med_s": round(
            statistics.median([r["ttfp_s"] for r in load["results"] if r.get("ttfp_s")] or [0]), 1
        ),
        "total_med_s": round(
            statistics.median([r["total_s"] for r in load["results"] if r.get("total_s")] or [0]), 1
        ),
        **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in snap.items()},
    }
    record_load_test(cell, load)
    print(json.dumps(row, indent=2))
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cells", help="comma-separated cell names to run (default: all + bonus)")
    ap.add_argument("--bonus-only", action="store_true")
    ap.add_argument("--skip-bonus", action="store_true")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    cells: list[dict[str, Any]] = [] if args.bonus_only else list(GRID)
    if not args.skip_bonus or args.bonus_only:
        cells.append(BONUS)
    if args.cells:
        wanted = {c.strip() for c in args.cells.split(",")}
        cells = [c for c in cells if c["cell"] in wanted]

    rows = []
    for cell in cells:
        rows.append(run_cell(cell))
        (OUT / "summary.json").write_text(json.dumps(rows, indent=2))

    print("\ncell        W  S   conc  predicted  measured  err%   wait_p95  ok/15")
    for r in rows:
        print(
            f"{r['cell']:<11} {r['workers']}  {r['stub_s']:<3} {r['conc']:<5} "
            f"{r['predicted_makespan_s']:>8}s {r['makespan_s']:>8}s {r['error_pct']:>5} "
            f"{str(r['wait_p95_s']):>9} {r['ok']:>4}/{r['users']}"
        )
    print(f"\nwrote {OUT / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
