"""s40 M1: the worker's job loop — frames out, ack last, deadlines honoured.

Unit-level with a fake redis: what matters is the wire contract the backend
relay depends on — every progress/plan/page frame lands on the job's stream,
queue_meta precedes result, XACK+XDEL happen after the result frame exists,
and an expired job is dropped with an error frame instead of burning work.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

from agent import main as agent_main
from agent import worker


class FakeRedis:
    def __init__(self) -> None:
        self.frames: list[tuple[str, str, dict[str, Any]]] = []  # (key, event, data)
        self.acked: list[str] = []
        self.deleted: list[str] = []
        self.kv: dict[str, str] = {}

    async def xadd(self, key: str, fields: dict[str, str]) -> None:
        self.frames.append((key, fields["event"], json.loads(fields["data"])))

    async def expire(self, key: str, ttl: int) -> None:
        pass

    async def xack(self, stream: str, group: str, entry_id: str) -> None:
        self.acked.append(entry_id)

    async def xdel(self, stream: str, entry_id: str) -> None:
        self.deleted.append(entry_id)

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)


def _job_fields(deadline_offset_s: float = 240) -> dict[str, str]:
    return {
        "job": json.dumps(
            {
                "job_id": "job-1",
                "enqueued_ms": int(time.time() * 1000) - 1500,
                "deadline_ts": time.time() + deadline_offset_s,
                "request": {
                    "question": "q",
                    "user": {"id": "00000000-0000-0000-0000-000000000001", "role": "user"},
                    "dataset_slug": "nsw_sales",
                },
            }
        )
    }


def test_process_job_publishes_frames_then_acks(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_answer(
        body: Any, progress: asyncio.Queue[dict[str, Any]] | None = None
    ) -> Any:
        assert progress is not None
        progress.put_nowait({"event": "plan", "pages": []})
        progress.put_nowait({"n": 1, "action": "step"})

        class _Result:
            def model_dump_json(self) -> str:
                return json.dumps({"answer": "42", "engine": "stub"})

        return _Result()

    monkeypatch.setattr(agent_main, "_answer", fake_answer)
    r = FakeRedis()
    asyncio.run(worker.process_job(r, "1-0", _job_fields()))

    events = [e for _k, e, _d in r.frames]
    assert events == ["plan", "progress", "queue_meta", "result"]
    meta = r.frames[2][2]
    assert meta["worker_id"] == worker.CONSUMER
    assert meta["deliveries"] == 1
    assert meta["queue_wait_ms"] >= 1500
    assert r.frames[3][2]["answer"] == "42"
    # At-least-once: acked (and deleted, so XLEN stays the depth) exactly once.
    assert r.acked == ["1-0"] and r.deleted == ["1-0"]


def test_process_job_drops_expired_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    async def must_not_run(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("expired job must never reach _answer")

    monkeypatch.setattr(agent_main, "_answer", must_not_run)
    r = FakeRedis()
    asyncio.run(worker.process_job(r, "1-0", _job_fields(deadline_offset_s=-5)))

    events = [e for _k, e, _d in r.frames]
    assert events == ["error"]
    assert r.acked == ["1-0"] and r.deleted == ["1-0"]


def test_process_job_failure_still_answers_the_relay(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(body: Any, progress: Any = None) -> Any:
        raise RuntimeError("sandbox exploded")

    monkeypatch.setattr(agent_main, "_answer", boom)
    r = FakeRedis()
    asyncio.run(worker.process_job(r, "1-0", _job_fields()))

    # Heartbeat status frames may precede the error (the loop only notices the
    # dead task on its next tick) — what matters is error, and nothing after it.
    events = [e for _k, e, _d in r.frames if e != "status"]
    assert events == ["error"]
    error = next(d for _k, e, d in r.frames if e == "error")
    assert "sandbox exploded" in error["detail"]
    assert r.acked == ["1-0"]
