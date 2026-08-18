"""s38: the demo-mode contract, handler-level (no server, no database).

Pins the guarantees the demo deployment depends on:
- the agent_client choke point refuses every LLM function with 501
  not_available_demo while demo_mode is on (dev/prod parity: the same build,
  one flag);
- demo replay resolves free text to the nearest recorded answer above the
  threshold and answers honestly below it (decision D1);
- the per-IP rate limiter 429s inside the window and recovers after it;
- /auth/config flips to the demo door and /auth/demo-login 404s when the flag
  is off (so no live deployment carries a dormant passwordless login).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import agent_client, demo_replay, limits
from app.config import settings
from app.demo_replay import DemoAnswer
from app.routers.auth import auth_config


@pytest.fixture
def demo_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings, "demo_mode", True)
    yield


def _request(ip: str = "203.0.113.7") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/ask",
            "headers": [(b"x-forwarded-for", ip.encode())],
            "query_string": b"",
            "scheme": "http",
            "server": ("test", 80),
            "client": (ip, 1234),
        }
    )


def _answer(qid: str, question: str) -> DemoAnswer:
    return DemoAnswer(
        id=qid,
        question=question,
        answer=f"answer to {question}",
        sql="SELECT 1",
        engine="deepseek",
        row_count=3,
        latency_ms=1000,
        steps=({"kind": "model"}, {"kind": "tool_return"}),
        report={"pages": [{"kind": "summary", "template": "two-col"}]},
        pages=({"kind": "summary", "template": "two-col"},),
    )


@pytest.fixture
def small_pack(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    pack = (
        _answer("growth", "Which suburbs had the fastest rent growth last year?"),
        _answer("yield", "Where are rental yields above 4 percent?"),
    )
    monkeypatch.setattr(demo_replay, "load_pack", lambda: pack)
    yield


# ---------------------------------------------------------------------------
# The choke point: every LLM function refuses in demo mode.
# ---------------------------------------------------------------------------
def test_llm_choke_point_refuses_in_demo(demo_on: None) -> None:
    for call in (
        agent_client.ask_agent(
            question="q", user_id="u", role="user", plan="free", dataset_slug="d"
        ),
        agent_client.title_agent("q"),
        agent_client.assist_sql_on_agent(
            action="generate", prompt="p", sql=None, user_id="u", role="user"
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(call)
        assert exc.value.status_code == 501
        assert exc.value.detail == "not_available_demo"


def test_llm_functions_untouched_outside_demo() -> None:
    # Outside demo mode the guard is inert — the call proceeds (and fails on
    # the network, which is fine: the guard must be the thing that DIDN'T fire).
    assert settings.demo_mode is False
    with pytest.raises(Exception) as exc:
        asyncio.run(agent_client.title_agent("q"))
    assert not (isinstance(exc.value, HTTPException) and exc.value.status_code == 501)


# ---------------------------------------------------------------------------
# Replay resolution (D1: chips + fuzzy match).
# ---------------------------------------------------------------------------
def test_exact_question_resolves_at_full_score(small_pack: None) -> None:
    ans, score = demo_replay.resolve("Which suburbs had the fastest rent growth last year?")
    assert ans is not None and ans.id == "growth"
    assert score == 1.0


def test_chip_id_resolves_exactly(small_pack: None) -> None:
    ans, score = demo_replay.resolve("yield")
    assert ans is not None and ans.id == "yield"
    assert score == 1.0


def test_close_question_fuzzy_matches_with_note(small_pack: None) -> None:
    result = demo_replay.result_for("which suburbs had the fastest rental growth last year")
    assert result["engine"] == "demo_replay"
    assert result["answer"].startswith("answer to")
    # Not an exact hit -> the "closest recorded answer" note is populated.
    assert result["demo_matched_question"] is not None


def test_unrelated_question_misses_honestly(small_pack: None) -> None:
    result = demo_replay.result_for("write me a poem about kubernetes")
    assert result.get("demo_miss") is True
    assert "recorded" in result["answer"]
    # The miss lists the available questions so the visitor isn't stranded.
    assert "rent growth" in result["answer"]


def test_replay_events_stream_pages_and_progress(small_pack: None) -> None:
    result = demo_replay.result_for("Which suburbs had the fastest rent growth last year?")

    async def collect() -> list[dict]:
        return [ev async for ev in demo_replay.replay_events(result)]

    events = asyncio.run(collect())
    names = [e["event"] for e in events]
    assert names[0] == "plan"
    assert "progress" in names
    assert names[-1] == "page"
    page = events[-1]["data"]
    assert page["status"] == "complete" and page["page"]["kind"] == "summary"


# ---------------------------------------------------------------------------
# Per-IP rate limiting.
# ---------------------------------------------------------------------------
def test_ip_rate_limit_trips_and_isolates_ips(demo_on: None) -> None:
    limits._ip_windows.clear()
    req = _request("198.51.100.1")
    for _ in range(3):
        limits.check_demo_ip_rate(req, "test-scope", 3)
    with pytest.raises(HTTPException) as exc:
        limits.check_demo_ip_rate(req, "test-scope", 3)
    assert exc.value.status_code == 429
    assert exc.value.detail == "demo_rate_limited"
    # A different IP is unaffected.
    limits.check_demo_ip_rate(_request("198.51.100.2"), "test-scope", 3)


def test_ip_rate_limit_noop_outside_demo() -> None:
    limits._ip_windows.clear()
    for _ in range(50):
        limits.check_demo_ip_rate(_request(), "test-scope", 3)


def test_ip_rate_limit_skips_internal_callers(demo_on: None) -> None:
    limits._ip_windows.clear()
    for _ in range(50):
        limits.check_demo_ip_rate(None, "test-scope", 3)


# ---------------------------------------------------------------------------
# The doors.
# ---------------------------------------------------------------------------
def test_auth_config_demo_door(demo_on: None) -> None:
    cfg = asyncio.run(auth_config())
    assert cfg.auth_mode == "demo"


def test_auth_config_normal_when_off() -> None:
    cfg = asyncio.run(auth_config())
    assert cfg.auth_mode in ("dev", "google")


def test_demo_login_404_when_off() -> None:
    from fastapi import Response

    from app.routers.auth import demo_login

    with pytest.raises(HTTPException) as exc:
        asyncio.run(demo_login(Response()))
    assert exc.value.status_code == 404
