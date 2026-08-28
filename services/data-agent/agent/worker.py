"""s40 M1: queue worker — the data-agent image running a consumer loop.

``python -m agent.worker``: warm up, join the ``workers`` consumer group as
``consumer-$HOSTNAME``, then loop — blocking-pop one job, drop it if its
deadline already passed, otherwise run the exact ``_answer`` path the HTTP
service runs, publishing every progress/plan/page frame to the job's
``agent:frames:{job_id}`` stream for the backend to relay. The ``result``
frame is published before the XACK (at-least-once: ack LAST), and the job
entry is XDEL'd after the ack so ``XLEN agent:jobs`` stays an honest queue
depth for admission control.

One job at a time per worker by default (WORKER_CONCURRENCY=1 — exact
capacity math: one replica = one answer-slot). WORKER_CONCURRENCY=N lets one
process interleave N react loops, overlapping their LLM waits (s41 D2).
SIGTERM finishes the in-flight jobs, then exits.
"""

from __future__ import annotations

import asyncio
import json
import signal
import socket
import time
from typing import Any, cast

import redis.asyncio as aioredis
from prometheus_client import Counter, Histogram, start_http_server

from .config import settings
from .queue import (
    CONSUMER_GROUP,
    DLQ_STREAM,
    JOBS_STREAM,
    cancel_key,
    client,
    ensure_group,
    frames_stream,
    publish_frame,
)

CONSUMER = f"consumer-{socket.gethostname()}"

JOBS = Counter("dataqa_worker_jobs_total", "Jobs processed by outcome", ["outcome"])
REDELIVERIES = Counter(
    "dataqa_worker_redeliveries_total", "Pending entries reclaimed from dead workers"
)
SERVICE_SECONDS = Histogram(
    "dataqa_worker_service_seconds",
    "Wall-clock seconds spent answering one job (excludes queue wait)",
    buckets=(1, 5, 10, 20, 30, 45, 60, 90, 120, 180, 240),
)


async def _finish(r: Any, entry_id: str) -> None:
    """At-least-once bookkeeping: ack (only after a result/error frame exists),
    then delete the entry so XLEN stays the admission-control depth."""
    await r.xack(JOBS_STREAM, CONSUMER_GROUP, entry_id)
    await r.xdel(JOBS_STREAM, entry_id)


async def process_job(
    r: Any, entry_id: str, fields: dict[str, str], *, deliveries: int = 1
) -> None:
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
        await _finish(r, entry_id)
        JOBS.labels(outcome="expired").inc()
        return

    # s40 M2: the relay flags cancel when its client goes away — and also right
    # after it consumed a result. Either way, a job whose flag is already up
    # (disconnect, or a redelivery of already-answered work) must not run.
    if await r.get(cancel_key(job_id)):
        await _finish(r, entry_id)
        JOBS.labels(outcome="cancelled").inc()
        return

    if deliveries > 1:
        # s40 M2: redelivery restarts from frame zero. Truncate the dead
        # worker's partial frames, then tell the client so it clears them
        # instead of rendering two half-answers.
        await r.xtrim(frames_stream(job_id), maxlen=0)
        await publish_frame(r, job_id, "status", {"state": "restarted", "deliveries": deliveries})

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
                # Heartbeat tick: keep the relay's XREAD loop (and the client's
                # SSE) warm, checkpoint the cancel flag, and re-claim our own
                # pending entry so its idle clock resets — the reaper only ever
                # reclaims entries whose worker stopped heartbeating (died).
                if not cancelled and await r.get(cancel_key(job_id)):
                    cancelled = True
                    task.cancel()
                try:
                    await r.xclaim(
                        JOBS_STREAM,
                        CONSUMER_GROUP,
                        CONSUMER,
                        min_idle_time=0,
                        message_ids=[entry_id],
                        justid=True,
                    )
                except Exception:  # noqa: BLE001 — a failed heartbeat is not fatal
                    pass
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
                "deliveries": deliveries,
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
        await _finish(r, entry_id)
        print(f"[worker {CONSUMER}] job {job_id} {outcome} wait={queue_wait_ms}ms")


async def reap(r: Any) -> list[tuple[str, dict[str, str], int]]:
    """s40 M2: reclaim pending entries whose worker died mid-job.

    XAUTOCLAIM hands over entries idle past REAPER_IDLE_MS (healthy workers
    reset their idle clock every heartbeat). A reclaimed entry past
    MAX_DELIVERIES is poison: it goes to the DLQ stream with an error frame to
    the client, never to another worker. Returns the claimed jobs this worker
    should now run, with their delivery counts.
    """
    try:
        claimed = await r.xautoclaim(
            JOBS_STREAM,
            CONSUMER_GROUP,
            CONSUMER,
            min_idle_time=settings.reaper_idle_ms,
            start_id="0-0",
            count=8,
        )
    except Exception:  # noqa: BLE001 — no group yet / redis blip; next tick retries
        return []
    entries = claimed[1] if isinstance(claimed, (list, tuple)) and len(claimed) > 1 else []
    todo: list[tuple[str, dict[str, str], int]] = []
    for entry_id, fields in entries:
        if fields is None:  # entry XDEL'd between claim and read
            await _finish(r, entry_id)
            continue
        pending = await r.xpending_range(
            JOBS_STREAM, CONSUMER_GROUP, min=entry_id, max=entry_id, count=1
        )
        deliveries = int(pending[0]["times_delivered"]) if pending else 1
        if deliveries > settings.max_deliveries:
            job = json.loads(fields["job"])
            job_id = str(job.get("job_id") or "unknown")
            await r.xadd(
                DLQ_STREAM,
                {"job": fields["job"], "deliveries": str(deliveries), "worker": CONSUMER},
            )
            await publish_frame(
                r,
                job_id,
                "error",
                {"detail": f"job failed {deliveries - 1} times and was dead-lettered"},
            )
            await _finish(r, entry_id)
            JOBS.labels(outcome="dead_lettered").inc()
            REDELIVERIES.inc()
            continue
        REDELIVERIES.inc()
        todo.append((entry_id, fields, deliveries))
    return todo


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

    # s41 D2: a bounded set of in-flight jobs. cap=1 (default) is the serial
    # worker — one job occupies the slot, LLM waits included. cap>1 interleaves
    # react loops on one event loop, overlapping their LLM waits.
    cap = max(1, settings.worker_concurrency)
    running: set[asyncio.Task[None]] = set()

    async def _run(entry_id: str, fields: dict[str, str], deliveries: int = 1) -> None:
        try:
            await process_job(r, entry_id, fields, deliveries=deliveries)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — one bad job must not kill the loop
            print(f"[worker {CONSUMER}] job task crashed: {exc}")

    def _spawn(entry_id: str, fields: dict[str, str], deliveries: int = 1) -> None:
        task = asyncio.ensure_future(_run(entry_id, fields, deliveries))
        running.add(task)
        task.add_done_callback(running.discard)

    print(f"[worker {CONSUMER}] consuming {JOBS_STREAM} (group {CONSUMER_GROUP}, slots {cap})")
    reap_backlog: list[tuple[str, dict[str, str], int]] = []
    while not stopping.is_set():
        if len(running) >= cap:
            await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            continue
        # s40 M2: sweep for orphaned work before blocking on fresh work. Claimed
        # entries beyond the cap stay in reap_backlog (still owned by this
        # consumer in the PEL) instead of being spawned past the concurrency
        # limit — drained as slots free up on later iterations.
        if not reap_backlog:
            reap_backlog.extend(await reap(r))
        while reap_backlog and len(running) < cap:
            entry_id, fields, deliveries = reap_backlog.pop(0)
            _spawn(entry_id, fields, deliveries)
        free = cap - len(running)
        if free <= 0:
            continue
        # decode_responses=True means str fields at runtime; the redis stubs
        # don't carry that through, hence the cast.
        try:
            resp = cast(
                "list[tuple[str, list[tuple[str, dict[str, str]]]]]",
                await r.xreadgroup(
                    CONSUMER_GROUP, CONSUMER, {JOBS_STREAM: ">"}, count=free, block=5000
                ),
            )
        except aioredis.TimeoutError:
            # redis-py 8.x raises instead of returning empty when a blocking
            # read expires with nothing to deliver — an idle tick, not a fault.
            continue
        if not resp:
            continue
        for _stream, entries in resp:
            for entry_id, fields in entries:
                _spawn(entry_id, fields)
    if running:
        # Graceful SIGTERM: finish the in-flight jobs, then exit.
        await asyncio.wait(running)
    print(f"[worker {CONSUMER}] stopped")


if __name__ == "__main__":
    asyncio.run(main())
