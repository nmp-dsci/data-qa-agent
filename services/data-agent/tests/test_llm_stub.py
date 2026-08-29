"""s40 (D2): LLM_STUB forces the deterministic stub, paced across STUB_LATENCY_S.

The load experiments (E2-E8, C-series) need a zero-spend workload whose runs
keep the timing *shape* of a real answer: the provider path must never run,
progress frames must trickle out during the window, and the answer must not be
flagged degraded (a forced stub is configuration, not a failure).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from agent import main
from agent.config import settings
from agent.main import AskRequest, UserCtx


def _body() -> AskRequest:
    return AskRequest(
        question="How many suburbs do we have?",
        user=UserCtx(id="00000000-0000-0000-0000-000000000001", role="user"),
    )


def test_llm_stub_skips_provider_and_paces_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("provider path must not run under LLM_STUB")

    async def fake_run_select(sql: str, *, user_id: str) -> dict[str, Any]:
        return {"sql": sql, "columns": ["c"], "rows": [[1]], "row_count": 1}

    monkeypatch.setattr(main, "answer_with_sandbox", boom)
    monkeypatch.setattr(main, "run_select", fake_run_select)
    monkeypatch.setattr(settings, "llm_stub", True)
    monkeypatch.setattr(settings, "stub_latency_s", 0.05)

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    started = time.monotonic()
    out = asyncio.run(main._answer(_body(), progress=queue))

    assert out.engine == "stub"
    # A forced stub is the configured behaviour, not a degradation.
    assert out.degraded is False
    # The pacing actually consumed (at least) the configured window.
    assert time.monotonic() - started >= 0.05

    frames: list[dict[str, Any]] = []
    while not queue.empty():
        frames.append(queue.get_nowait())
    # Plan first (the stub path always declares the page slots)...
    assert frames[0].get("event") == "plan"
    # ...then paced progress frames during the window.
    assert any(f.get("action") == "Working (stub)" for f in frames)


def test_llm_stub_zero_latency_is_instant(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_select(sql: str, *, user_id: str) -> dict[str, Any]:
        return {"sql": sql, "columns": ["c"], "rows": [[1]], "row_count": 1}

    monkeypatch.setattr(main, "run_select", fake_run_select)
    monkeypatch.setattr(settings, "llm_stub", True)
    monkeypatch.setattr(settings, "stub_latency_s", 0.0)

    out = asyncio.run(main._answer(_body()))
    assert out.engine == "stub"
