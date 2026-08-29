"""s40 M1: Redis Streams job queue — stream names and frame helpers.

One primitive carries everything (D1): a shared ``agent:jobs`` stream read by a
consumer group of workers (ack/pending/redelivery semantics), one small
``agent:frames:{job_id}`` stream per job that the worker publishes SSE frames
into and the backend relays, an ``agent:dlq`` stream for poison jobs, and an
``agent:cancel:{job_id}`` flag for client disconnects.

The backend duplicates these names in ``app/queue_client.py`` — the two files
are the producer and consumer of one wire contract, so a change here must land
there too (same discipline as the units.py/units.ts pair).
"""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis

from .config import settings

JOBS_STREAM = "agent:jobs"
CONSUMER_GROUP = "workers"
DLQ_STREAM = "agent:dlq"

# A finished job's frames are only useful while a relay is (re)reading them —
# an hour comfortably outlives any deadline, then Redis reclaims the memory.
FRAMES_TTL_S = 3600


def frames_stream(job_id: str) -> str:
    return f"agent:frames:{job_id}"


def cancel_key(job_id: str) -> str:
    return f"agent:cancel:{job_id}"


_client: aioredis.Redis | None = None


def client() -> aioredis.Redis:
    """Process-wide client (worker). decode_responses so fields are str."""
    global _client
    if _client is None:
        _client = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def publish_frame(r: aioredis.Redis, job_id: str, event: str, data: dict[str, Any]) -> None:
    """Append one SSE-shaped frame to the job's stream and refresh its TTL."""
    key = frames_stream(job_id)
    await r.xadd(key, {"event": event, "data": json.dumps(data)})
    await r.expire(key, FRAMES_TTL_S)


async def ensure_group(r: aioredis.Redis) -> None:
    """Create the consumer group (and stream) if this is the first boot."""
    try:
        await r.xgroup_create(JOBS_STREAM, CONSUMER_GROUP, id="$", mkstream=True)
    except aioredis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise
