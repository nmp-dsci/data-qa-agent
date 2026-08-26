"""s40 M1: backend side of the queue seam — admission, enqueue, frame relay.

``QUEUE_MODE=on`` swaps the chat path's source: instead of holding an httpx SSE
stream to the agent, ``/ask/stream`` enqueues one job onto the shared
``agent:jobs`` stream and relays the worker's frames from the per-job
``agent:frames:{job_id}`` stream. The frame contract is exactly what
``agent_client.ask_agent_stream`` yields — ``{"event": name, "data": dict}`` —
so the caller cannot tell which path answered.

Stream names duplicate ``services/data-agent/agent/queue.py`` — producer and
consumer of one wire contract; change both together (units.py/units.ts rule).
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any, cast

import redis.asyncio as aioredis
from fastapi import HTTPException

from . import metrics
from .config import settings

JOBS_STREAM = "agent:jobs"
FRAMES_PREFIX = "agent:frames:"
CANCEL_PREFIX = "agent:cancel:"


class QueueError(RuntimeError):
    """Redis unavailable or the relay gave up — callers degrade like an httpx error."""


_client: aioredis.Redis | None = None


def client() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def queue_depth() -> int:
    """Jobs waiting or in flight. Workers XDEL after XACK, so XLEN is honest."""
    try:
        return int(await client().xlen(JOBS_STREAM))
    except Exception as exc:  # noqa: BLE001 — surface as a queue failure, not a 500
        raise QueueError(f"redis unavailable: {exc}") from exc


async def check_admission() -> int:
    """Bounded queue: refuse work early and cheaply past QUEUE_MAX_DEPTH.

    Returns the current depth (= this job's queue position) so the caller can
    show ``queued (position N)``. Raises a clean 429 with Retry-After when full.
    """
    depth = await queue_depth()
    if depth >= settings.queue_max_depth:
        metrics.QUEUE_SHED.inc()
        # A rough single-slot drain time; honest enough for a client backoff.
        raise HTTPException(
            status_code=429,
            detail="The assistant is at capacity right now — please retry shortly.",
            headers={"Retry-After": "30"},
        )
    return depth


async def enqueue(*, question: str, user_id: str, role: str, plan: str, dataset_slug: str) -> str:
    """XADD one job; returns the job id the frames stream is keyed by."""
    job_id = str(uuid.uuid4())
    payload = {
        "job_id": job_id,
        "enqueued_ms": int(time.time() * 1000),
        "deadline_ts": time.time() + settings.job_deadline_s,
        "request": {
            "question": question,
            "user": {"id": user_id, "role": role, "plan": plan},
            "dataset_slug": dataset_slug,
        },
    }
    try:
        await client().xadd(JOBS_STREAM, {"job": json.dumps(payload)})
    except Exception as exc:  # noqa: BLE001
        raise QueueError(f"enqueue failed: {exc}") from exc
    metrics.QUEUE_ENQUEUED.inc()
    return job_id


async def ask_agent_stream_queued(
    *, question: str, user_id: str, role: str, plan: str, dataset_slug: str
) -> AsyncIterator[dict[str, Any]]:
    """Queue-mode twin of ``agent_client.ask_agent_stream`` — same frames.

    Enqueues, emits one ``status`` frame carrying the queue position, then
    relays the worker's frames. The ``queue_meta`` frame (wait, worker id,
    deliveries) is folded into the ``result`` payload so persistence sees it
    as ordinary result fields.
    """
    position = await check_admission()
    job_id = await enqueue(
        question=question, user_id=user_id, role=role, plan=plan, dataset_slug=dataset_slug
    )
    yield {"event": "status", "data": {"state": "queued", "position": position + 1}}

    r = client()
    frames_key = f"{FRAMES_PREFIX}{job_id}"
    last_id = "0-0"
    # The relay outlives the job deadline slightly: a worker that picked the job
    # up just under the wire still gets to finish streaming its answer.
    give_up_at = time.time() + settings.job_deadline_s + 60
    queue_meta: dict[str, Any] = {}
    try:
        while True:
            try:
                # decode_responses=True means str fields at runtime; the redis
                # stubs don't carry that through, hence the cast.
                resp = cast(
                    "list[tuple[str, list[tuple[str, dict[str, str]]]]]",
                    await r.xread({frames_key: last_id}, count=64, block=2000),
                )
            except aioredis.TimeoutError:
                # redis-py 8.x raises instead of returning empty when a
                # blocking read expires with no entries — it's just "no
                # frames yet", not a failure.
                resp = []
            except Exception as exc:  # noqa: BLE001
                raise QueueError(f"relay read failed: {exc}") from exc
            if not resp:
                if time.time() > give_up_at:
                    yield {
                        "event": "error",
                        "data": {"detail": "queued job produced no result before the deadline"},
                    }
                    return
                yield {"event": "status", "data": {"state": "working"}}
                continue
            for entry_id, fields in resp[0][1]:
                last_id = entry_id
                event = fields["event"]
                data = json.loads(fields["data"])
                if event == "queue_meta":
                    queue_meta = data
                    if "queue_wait_ms" in data:
                        metrics.QUEUE_WAIT_SECONDS.observe(data["queue_wait_ms"] / 1000)
                    continue
                if event == "result":
                    data.update(queue_meta)
                    yield {"event": "result", "data": data}
                    return
                if event == "error":
                    yield {"event": "error", "data": data}
                    return
                yield {"event": event, "data": data}
    finally:
        # Client gone (or done): flag the job so a worker mid-answer stops
        # spending on it. Harmless after completion — the key just expires.
        try:
            await r.set(f"{CANCEL_PREFIX}{job_id}", "1", ex=600)
        except Exception:  # noqa: BLE001 — best-effort; never mask the real exit
            pass


async def ask_agent_queued(
    *, question: str, user_id: str, role: str, plan: str, dataset_slug: str
) -> dict[str, Any]:
    """Blocking twin of ``agent_client.ask_agent``: enqueue, wait for the result.

    The s41 open question ("does /ask queue too?") is answered yes — Slack and
    service-account callers get backpressure for free through this one path.
    """
    async for ev in ask_agent_stream_queued(
        question=question, user_id=user_id, role=role, plan=plan, dataset_slug=dataset_slug
    ):
        if ev["event"] == "result":
            return dict(ev["data"])
        if ev["event"] == "error":
            raise QueueError(str(ev["data"].get("detail") or "queued job failed"))
    raise QueueError("queued job stream ended without a result")
