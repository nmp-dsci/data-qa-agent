"""s40 M1: queue worker — the data-agent image running a consumer loop.

``python -m agent.worker``: warm up, join the ``workers`` consumer group as
``consumer-$HOSTNAME``, then loop — blocking-pop one job, drop it if its
deadline already passed, otherwise run the exact ``_answer`` path the HTTP
service runs, publishing every progress/plan/page frame to the job's
``agent:frames:{job_id}`` stream for the backend to relay. The ``result``
frame is published before the XACK (at-least-once: ack LAST), and the job
entry is XDEL'd after the ack so ``XLEN agent:jobs`` stays an honest queue
depth for admission control.

One job at a time per worker — the sandbox serializes anyway. SIGTERM finishes
the current job, then exits.
"""

from __future__ import annotations

import asyncio
import json
import signal
import socket
import time
from typing import Any, cast

from prometheus_client import Counter, Histogram, start_http_server

from .config import settings
from .queue import (
    CONSUMER_GROUP,
    JOBS_STREAM,
    cancel_key,
    client,
    ensure_group,
    publish_frame,
)

CONSUMER = f"consumer-{socket.gethostname()}"

JOBS = Counter("dataqa_worker_jobs_total", "Jobs processed by outcome", ["outcome"])
SERVICE_SECONDS = Histogram(
    "dataqa_worker_service_seconds",
    "Wall-clock seconds spent answering one job (excludes queue wait)",
    buckets=(1, 5, 10, 20, 30, 45, 60, 90, 120, 180, 240),
)


async def process_job(r: Any, entry_id: str, fields: dict[str, str]) -> None:
    """Run one job end-to-end; always leaves a result/error frame behind."""
    # Deferred: importing main pulls FastAPI + logfire config; worker pays it
    # once at first job (after warm-up), never per job.
    from .main import AskRequest, _answer

    job = json.loads(fields["job"])
    job_id = str(job["job_id"])
    queue_wait_ms = max(0, int(time.time() * 1000) - int(job.get("enqueued_ms") or 0))

    deadline_ts = float(job.get("deadline_ts") or 0)
    if deadline_ts and time.time() > deadline_ts:
        # Deadline propagation: the client is gone — never burn work for it.
        await publish_frame(
            r, job_id, "error", {"detail": "job expired in the queue before a worker was free"}
        )
        await r.xack(JOBS_STREAM, CONSUMER_GROUP, entry_id)
        await r.xdel(JOBS_STREAM, entry_id)
        JOBS.labels(outcome="expired").inc()
        return

    started = time.perf_counter()
    outcome = "ok"
    try:
        body = AskRequest(**job["request"])
        progress: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        task = asyncio.ensure_future(_answer(body, progress=progress))
        cancelled = False
        while not task.done() or not progress.empty():
            try:
                event = await asyncio.wait_for(progress.get(), timeout=2.0)
            except TimeoutError:
                # Heartbeat keeps the relay's XREAD loop (and the client's SSE)
                # warm while a long step runs; also the cancel checkpoint.
                if not cancelled and await r.get(cancel_key(job_id)):
                    cancelled = True
                    task.cancel()
                await publish_frame(r, job_id, "status", {"state": "working"})
                continue
            name = event.pop("event", None)
            await publish_frame(r, job_id, name if name in ("plan", "page") else "progress", event)
        if cancelled:
            outcome = "cancelled"
            JOBS.labels(outcome="cancelled").inc()
            return
        result = task.result()
        await publish_frame(
            r,
            job_id,
            "queue_meta",
            {
                "queue_wait_ms": queue_wait_ms,
                "worker_id": CONSUMER,
                "deliveries": int(job.get("deliveries") or 1),
            },
        )
        await publish_frame(r, job_id, "result", json.loads(result.model_dump_json()))
        JOBS.labels(outcome="ok").inc()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — a failed job must still answer the relay
        outcome = "error"
        await publish_frame(r, job_id, "error", {"detail": str(exc)})
        JOBS.labels(outcome="error").inc()
    finally:
        SERVICE_SECONDS.observe(time.perf_counter() - started)
        # At-least-once: ack only after the result/error frame exists, then
        # delete the entry so XLEN stays the admission-control depth.
        await r.xack(JOBS_STREAM, CONSUMER_GROUP, entry_id)
        await r.xdel(JOBS_STREAM, entry_id)
        print(f"[worker {CONSUMER}] job {job_id} {outcome} wait={queue_wait_ms}ms")


async def main() -> None:
    start_http_server(settings.worker_metrics_port)
    r = client()
    await ensure_group(r)

    # Warm before consuming (s40 M0): a fresh replica must not pay the ONNX +
    # pyodide cold start on its first real job.
    from .main import _warmup

    await _warmup()

    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopping.set)

    print(f"[worker {CONSUMER}] consuming {JOBS_STREAM} (group {CONSUMER_GROUP})")
    while not stopping.is_set():
        # decode_responses=True means str fields at runtime; the redis stubs
        # don't carry that through, hence the cast.
        resp = cast(
            "list[tuple[str, list[tuple[str, dict[str, str]]]]]",
            await r.xreadgroup(CONSUMER_GROUP, CONSUMER, {JOBS_STREAM: ">"}, count=1, block=5000),
        )
        if not resp:
            continue
        for _stream, entries in resp:
            for entry_id, fields in entries:
                # Graceful SIGTERM: finish this job, then the loop exits.
                await process_job(r, entry_id, fields)
    print(f"[worker {CONSUMER}] stopped")


if __name__ == "__main__":
    asyncio.run(main())
