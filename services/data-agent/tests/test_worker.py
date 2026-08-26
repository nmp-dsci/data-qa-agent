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
        self.trimmed: list[str] = []
        self.dlq: list[dict[str, str]] = []
        self.autoclaim_result: Any = ("0-0", [], [])
        self.pending_deliveries: int = 1

    async def xadd(self, key: str, fields: dict[str, str]) -> None:
        if key == worker.DLQ_STREAM:
            self.dlq.append(fields)
            return
        self.frames.append((key, fields["event"], json.loads(fields["data"])))

    async def expire(self, key: str, ttl: int) -> None:
        pass

    async def xack(self, stream: str, group: str, entry_id: str) -> None:
        self.acked.append(entry_id)

    async def xdel(self, stream: str, entry_id: str) -> None:
        self.deleted.append(entry_id)

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)

    async def xtrim(self, key: str, maxlen: int) -> None:
        self.trimmed.append(key)

    async def xclaim(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    async def xautoclaim(self, *args: Any, **kwargs: Any) -> Any:
        return self.autoclaim_result

    async def xpending_range(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return [{"times_delivered": self.pending_deliveries}]


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
    async def fake_answer(body: Any, progress: asyncio.Queue[dict[str, Any]] | None = None) -> Any:
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


def test_cancelled_job_never_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    """s40 M2: cancel flag up (client gone, or already answered) → no work."""

    async def must_not_run(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("cancelled job must never reach _answer")

    monkeypatch.setattr(agent_main, "_answer", must_not_run)
    r = FakeRedis()
    r.kv["agent:cancel:job-1"] = "1"
    asyncio.run(worker.process_job(r, "1-0", _job_fields()))
    assert r.frames == []
    assert r.acked == ["1-0"] and r.deleted == ["1-0"]


def test_redelivery_truncates_frames_and_flags_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    """s40 M2: deliveries>1 → old partial frames trimmed + status:restarted first."""

    async def fake_answer(body: Any, progress: Any = None) -> Any:
        class _Result:
            def model_dump_json(self) -> str:
                return json.dumps({"answer": "second try", "engine": "stub"})

        return _Result()

    monkeypatch.setattr(agent_main, "_answer", fake_answer)
    r = FakeRedis()
    asyncio.run(worker.process_job(r, "1-0", _job_fields(), deliveries=2))

    assert r.trimmed == ["agent:frames:job-1"]
    events = [e for _k, e, _d in r.frames if e != "status" or _d.get("state") == "restarted"]
    assert events[0] == "status"  # restarted announcement leads
    meta = next(d for _k, e, d in r.frames if e == "queue_meta")
    assert meta["deliveries"] == 2


def test_reap_dead_letters_poison_jobs() -> None:
    """s40 M2: a reclaimed entry past MAX_DELIVERIES goes to the DLQ, not a worker."""
    r = FakeRedis()
    r.autoclaim_result = ("0-0", [("9-0", _job_fields())], [])
    r.pending_deliveries = 4  # > max_deliveries (3)

    todo = asyncio.run(worker.reap(r))
    assert todo == []
    assert len(r.dlq) == 1 and r.dlq[0]["deliveries"] == "4"
    error = next(d for _k, e, d in r.frames if e == "error")
    assert "dead-lettered" in error["detail"]
    assert r.acked == ["9-0"] and r.deleted == ["9-0"]


def test_reap_hands_back_retryable_jobs() -> None:
    """s40 M2: under the poison threshold, a reclaimed job is simply re-run."""
    r = FakeRedis()
    fields = _job_fields()
    r.autoclaim_result = ("0-0", [("9-0", fields)], [])
    r.pending_deliveries = 2

    todo = asyncio.run(worker.reap(r))
    assert todo == [("9-0", fields, 2)]
    assert r.dlq == [] and r.acked == []
