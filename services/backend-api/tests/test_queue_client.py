"""s40 M1: backend queue seam — admission control and the frame relay.

Unit-level with a fake redis: a full queue sheds with a clean 429 +
Retry-After, an accepted job's frames relay verbatim, and the worker's
queue_meta frame is folded into the result payload so persistence sees
queue_wait_ms/worker_id/deliveries as ordinary result fields.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi import HTTPException

from app import queue_client


class FakeRedis:
    def __init__(self, depth: int = 0, frames: list[tuple[str, dict[str, Any]]] | None = None):
        self.depth = depth
        # Each relay poll returns the whole remaining backlog once, then None.
        self._frames = list(frames or [])
        self.enqueued: list[dict[str, Any]] = []
        self.kv: dict[str, str] = {}

    async def xlen(self, stream: str) -> int:
        return self.depth

    async def xadd(self, stream: str, fields: dict[str, str]) -> None:
        self.enqueued.append(json.loads(fields["job"]))

    async def xread(self, streams: dict[str, str], count: int, block: int) -> Any:
        if not self._frames:
            return None
        batch = [
            (f"1-{i}", {"event": ev, "data": json.dumps(data)})
            for i, (ev, data) in enumerate(self._frames)
        ]
        self._frames = []
        return [("agent:frames:x", batch)]

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.kv[key] = value


@pytest.fixture(autouse=True)
def _reset_client(monkeypatch: pytest.MonkeyPatch):
    yield
    queue_client._client = None


def _use(r: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(queue_client, "_client", r)


def test_admission_sheds_with_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    _use(FakeRedis(depth=10), monkeypatch)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(queue_client.check_admission())
    assert exc.value.status_code == 429
    assert exc.value.headers and "Retry-After" in exc.value.headers


def test_admission_under_bound_returns_position(monkeypatch: pytest.MonkeyPatch) -> None:
    _use(FakeRedis(depth=3), monkeypatch)
    assert asyncio.run(queue_client.check_admission()) == 3


def test_relay_merges_queue_meta_into_result(monkeypatch: pytest.MonkeyPatch) -> None:
    frames = [
        ("progress", {"n": 1, "action": "step"}),
        ("page", {"index": 0, "status": "complete", "page": {}}),
        ("queue_meta", {"queue_wait_ms": 1200, "worker_id": "consumer-w1", "deliveries": 1}),
        ("result", {"answer": "42", "engine": "sandbox"}),
    ]
    r = FakeRedis(depth=0, frames=frames)
    _use(r, monkeypatch)

    async def collect() -> list[dict[str, Any]]:
        return [
            ev
            async for ev in queue_client.ask_agent_stream_queued(
                question="q", user_id="u1", role="user", plan="free", dataset_slug="nsw_sales"
            )
        ]

    events = asyncio.run(collect())
    names = [e["event"] for e in events]
    # queued-position status first; queue_meta is folded away, not relayed.
    assert names == ["status", "progress", "page", "result"]
    assert events[0]["data"]["state"] == "queued"
    result = events[-1]["data"]
    assert result["answer"] == "42"
    assert result["queue_wait_ms"] == 1200
    assert result["worker_id"] == "consumer-w1"
    assert result["deliveries"] == 1
    # One job actually enqueued, carrying the deadline for the worker to honour.
    assert len(r.enqueued) == 1 and r.enqueued[0]["deadline_ts"] > 0
    # The finally-block flagged cancel (harmless post-completion, vital mid-run).
    assert any(k.startswith("agent:cancel:") for k in r.kv)


def test_blocking_twin_returns_result(monkeypatch: pytest.MonkeyPatch) -> None:
    frames = [
        ("queue_meta", {"queue_wait_ms": 10, "worker_id": "consumer-w1", "deliveries": 1}),
        ("result", {"answer": "ok", "engine": "sandbox"}),
    ]
    _use(FakeRedis(depth=0, frames=frames), monkeypatch)
    out = asyncio.run(
        queue_client.ask_agent_queued(
            question="q", user_id="u1", role="user", plan="free", dataset_slug="nsw_sales"
        )
    )
    assert out["answer"] == "ok" and out["queue_wait_ms"] == 10


def test_worker_error_frame_raises_queue_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _use(FakeRedis(depth=0, frames=[("error", {"detail": "job expired"})]), monkeypatch)
    with pytest.raises(queue_client.QueueError, match="job expired"):
        asyncio.run(
            queue_client.ask_agent_queued(
                question="q", user_id="u1", role="user", plan="free", dataset_slug="nsw_sales"
            )
        )
