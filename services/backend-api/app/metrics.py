"""s40 M1 (D4): the backend's Prometheus registry, first-class not stretch.

Counters/histograms are observed where the events happen (queue_client);
the queue-depth gauge is polled by a background task started from the app
lifespan when QUEUE_MODE=on, so a scrape never blocks on Redis. ``/metrics``
in main.py serves the default registry.
"""

from __future__ import annotations

import asyncio
import contextlib

from prometheus_client import Counter, Gauge, Histogram

QUEUE_ENQUEUED = Counter("dataqa_queue_enqueued_total", "Jobs accepted onto agent:jobs")
QUEUE_SHED = Counter(
    "dataqa_queue_shed_total", "Requests refused at the door (429, depth >= bound)"
)
QUEUE_WAIT_SECONDS = Histogram(
    "dataqa_queue_wait_seconds",
    "Enqueue -> worker pickup, as reported by the worker's queue_meta frame",
    buckets=(0.05, 0.25, 1, 5, 15, 30, 60, 120, 240),
)
QUEUE_DEPTH = Gauge("dataqa_queue_depth", "Jobs waiting or in flight on agent:jobs")
# s41: poison jobs parked for a human — should be flat zero; any rise is a page.
QUEUE_DLQ_DEPTH = Gauge("dataqa_queue_dlq_depth", "Dead-lettered jobs on agent:dlq")


async def _poll_depth() -> None:
    from . import queue_client

    while True:
        try:
            QUEUE_DEPTH.set(await queue_client.queue_depth())
            QUEUE_DLQ_DEPTH.set(await queue_client.client().xlen("agent:dlq"))
        except Exception:  # noqa: BLE001 — a redis blip must not kill the poller
            pass
        await asyncio.sleep(2)


_poller: asyncio.Task[None] | None = None


def start_depth_poller() -> None:
    global _poller
    if _poller is None:
        _poller = asyncio.get_event_loop().create_task(_poll_depth())


async def stop_depth_poller() -> None:
    global _poller
    if _poller is not None:
        _poller.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _poller
        _poller = None
