"""s40 M3 (D3): the SSE prober k6 can't be — TTFP and queue UX, measured for real.

k6's http client buffers the whole response, so it cannot see when the FIRST
page frame arrived, whether the client was told its queue position, or whether
a mid-answer worker death surfaced as one clean ``status: restarted``. This
script opens N concurrent one-shot ``/ask/stream`` connections and reports,
per user and aggregated:

  * ttfp_s      — open -> first COMPLETE page frame (what SLO-B grades)
  * total_s     — open -> result frame
  * queued_pos  — the position the queued status frame announced (queue mode)
  * restarts    — status: restarted frames seen (E8 / kill-worker drills)
  * outcome     — ok | shed (429) | error | timeout

Usage (stack running; dev auth):
    uv run python scripts/loadgen_sse.py --users 10
    uv run python scripts/loadgen_sse.py --users 20 --base-url http://localhost:8010
    uv run python scripts/loadgen_sse.py --users 5 --json out.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from typing import Any

import httpx

QUESTIONS = [
    "What are the top growth suburbs for sale price and rent?",
    "Which suburbs have the highest rent growth?",
    "Show the sale price trend for houses in Hornsby",
    "Top suburbs by sale price growth?",
]


async def _token(client: httpx.AsyncClient, base_url: str, user: str) -> str:
    resp = await client.post(f"{base_url}/auth/dev-login", json={"username": user})
    resp.raise_for_status()
    return str(resp.json()["access_token"])


async def probe(
    client: httpx.AsyncClient, base_url: str, bearer: str, question: str, deadline_s: float
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "outcome": "timeout",
        "ttfp_s": None,
        "total_s": None,
        "queued_pos": None,
        "restarts": 0,
    }
    started = time.perf_counter()
    headers = {
        "Authorization": f"Bearer {bearer}",
        "Content-Type": "application/json",
        "X-Client-Channel": "load",
    }
    try:
        async with client.stream(
            "POST",
            f"{base_url}/ask/stream",
            json={"question": question, "conversation_id": None},
            headers=headers,
            timeout=httpx.Timeout(connect=10, read=deadline_s, write=10, pool=10),
        ) as resp:
            if resp.status_code == 429:
                out["outcome"] = "shed"
                out["total_s"] = time.perf_counter() - started
                return out
            resp.raise_for_status()
            event = ""
            async for line in resp.aiter_lines():
                if line.startswith("event: "):
                    event = line[7:].strip()
                    continue
                if not line.startswith("data: "):
                    continue
                try:
                    data = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                if event == "status" and isinstance(data, dict):
                    if data.get("state") == "queued" and out["queued_pos"] is None:
                        out["queued_pos"] = data.get("position")
                    if data.get("state") == "restarted":
                        out["restarts"] += 1
                elif event == "page" and data.get("status") == "complete":
                    if out["ttfp_s"] is None:
                        out["ttfp_s"] = time.perf_counter() - started
                elif event == "result":
                    out["outcome"] = "ok"
                    out["total_s"] = time.perf_counter() - started
                    return out
                elif event == "error":
                    out["outcome"] = "error"
                    out["error"] = data.get("detail")
                    out["total_s"] = time.perf_counter() - started
                    return out
    except httpx.HTTPError as exc:
        out["outcome"] = "error"
        out["error"] = str(exc)
        out["total_s"] = time.perf_counter() - started
    return out


def _fmt(values: list[float]) -> str:
    if not values:
        return "n/a"
    q = statistics.quantiles(values, n=20) if len(values) >= 2 else [values[0]] * 19
    return f"med={statistics.median(values):.1f}s p95={q[18]:.1f}s max={max(values):.1f}s"


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--users", type=int, default=5, help="concurrent one-shot users")
    ap.add_argument("--user", default="user1", help="dev-auth username")
    ap.add_argument("--deadline-s", type=float, default=300)
    ap.add_argument("--json", dest="json_out", help="also write per-user results here")
    args = ap.parse_args()

    async with httpx.AsyncClient() as client:
        bearer = await _token(client, args.base_url, args.user)
        started = time.perf_counter()
        results = await asyncio.gather(
            *(
                probe(client, args.base_url, bearer, QUESTIONS[i % len(QUESTIONS)], args.deadline_s)
                for i in range(args.users)
            )
        )
        makespan = time.perf_counter() - started

    ok = [r for r in results if r["outcome"] == "ok"]
    print(f"users={args.users} makespan={makespan:.1f}s")
    print(
        f"outcomes: ok={len(ok)} shed={sum(r['outcome'] == 'shed' for r in results)} "
        f"error={sum(r['outcome'] == 'error' for r in results)} "
        f"timeout={sum(r['outcome'] == 'timeout' for r in results)}"
    )
    print(f"ttfp:  {_fmt([r['ttfp_s'] for r in ok if r['ttfp_s'] is not None])}")
    print(f"total: {_fmt([r['total_s'] for r in ok if r['total_s'] is not None])}")
    queued = [r["queued_pos"] for r in results if r["queued_pos"] is not None]
    print(f"queued positions seen: {sorted(queued) if queued else 'none (direct path?)'}")
    restarts = sum(r["restarts"] for r in results)
    if restarts:
        print(f"restarts observed: {restarts}")
    if args.json_out:
        payload = {"users": args.users, "makespan_s": makespan, "results": results}
        with open(args.json_out, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"wrote {args.json_out}")


if __name__ == "__main__":
    asyncio.run(main())
