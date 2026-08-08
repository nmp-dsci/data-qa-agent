"""The @mention front door (s37 rung 3).

Like the slash command, this is a security boundary between the open internet
and LLM spend — so the negative paths (bad signature, closed endpoint, retry
replays, bot loops) matter more than the happy path. Everything here runs
in-process against the ASGI app with the delivery function faked: no DB, no
Slack, CI-safe.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import httpx
import pytest

import app.routers.integrations as integrations
from app.config import settings
from app.main import app

SECRET = "test-signing-secret"  # noqa: S105 - a fixture, not a credential
URL = "http://testserver/integrations/slack/events"


def _sig(body: bytes, timestamp: str) -> str:
    base = f"v0:{timestamp}:".encode() + body
    return "v0=" + hmac.new(SECRET.encode(), base, hashlib.sha256).hexdigest()


def _signed_headers(body: bytes, extra: dict[str, str] | None = None) -> dict[str, str]:
    ts = str(int(time.time()))
    return {
        "Content-Type": "application/json",
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": _sig(body, ts),
        **(extra or {}),
    }


def _mention_event(**overrides: Any) -> bytes:
    event = {
        "type": "app_mention",
        "user": "U123",
        "text": "<@UBOT> median rent in 2077",
        "channel": "C777",
        "ts": "1700000010.000100",
        **overrides,
    }
    return json.dumps({"type": "event_callback", "event": event}).encode()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> httpx.AsyncClient:
    monkeypatch.setattr(settings, "slack_signing_secret", SECRET)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


@pytest.fixture
def delivered(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []

    async def _fake(*args: Any) -> None:
        calls.append(args)

    monkeypatch.setattr(integrations, "_deliver_mention", _fake)
    return calls


async def test_url_verification_challenge_is_echoed(client: httpx.AsyncClient) -> None:
    body = json.dumps({"type": "url_verification", "challenge": "c0ffee"}).encode()
    resp = await client.post(URL, content=body, headers=_signed_headers(body))
    assert resp.status_code == 200
    assert resp.json() == {"challenge": "c0ffee"}


async def test_unsigned_request_is_rejected(client: httpx.AsyncClient) -> None:
    resp = await client.post(URL, content=_mention_event())
    assert resp.status_code == 401


async def test_unset_secret_closes_the_endpoint(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "slack_signing_secret", "")
    body = _mention_event()
    resp = await client.post(URL, content=body, headers=_signed_headers(body))
    assert resp.status_code == 404


async def test_mention_schedules_delivery_with_thread_fields(
    client: httpx.AsyncClient, delivered: list[tuple[Any, ...]]
) -> None:
    body = _mention_event(thread_ts="1700000000.000001")
    resp = await client.post(URL, content=body, headers=_signed_headers(body))
    assert resp.status_code == 200
    assert len(delivered) == 1
    channel, thread_ts, event_ts, text_in, asker = delivered[0]
    assert channel == "C777"
    assert thread_ts == "1700000000.000001"  # the thread root, not the mention
    assert event_ts == "1700000010.000100"
    assert "median rent in 2077" in text_in
    assert asker == {
        "user_id": "U123",
        "channel_id": "C777",
        "thread_ts": "1700000000.000001",
    }


async def test_top_level_mention_threads_under_itself(
    client: httpx.AsyncClient, delivered: list[tuple[Any, ...]]
) -> None:
    body = _mention_event()  # no thread_ts: a channel-top-level mention
    await client.post(URL, content=body, headers=_signed_headers(body))
    _, thread_ts, event_ts, _, _ = delivered[0]
    assert thread_ts == event_ts  # the answer starts a thread on the mention


async def test_retry_redelivery_is_acked_but_not_reanswered(
    client: httpx.AsyncClient, delivered: list[tuple[Any, ...]]
) -> None:
    body = _mention_event()
    resp = await client.post(
        URL, content=body, headers=_signed_headers(body, {"X-Slack-Retry-Num": "1"})
    )
    # 200 so Slack stops retrying; no second agent run for the same mention.
    assert resp.status_code == 200
    assert delivered == []


async def test_bot_mentions_are_ignored(
    client: httpx.AsyncClient, delivered: list[tuple[Any, ...]]
) -> None:
    body = _mention_event(bot_id="B999")  # a bot (or ourselves) — loop guard
    resp = await client.post(URL, content=body, headers=_signed_headers(body))
    assert resp.status_code == 200
    assert delivered == []


async def test_non_mention_events_are_ignored(
    client: httpx.AsyncClient, delivered: list[tuple[Any, ...]]
) -> None:
    body = json.dumps(
        {"type": "event_callback", "event": {"type": "message", "text": "hello"}}
    ).encode()
    resp = await client.post(URL, content=body, headers=_signed_headers(body))
    assert resp.status_code == 200
    assert delivered == []


def test_placeholder_bot_token_reads_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "slack_bot_token", "REPLACE_ME_VIA_CLI")
    assert settings.slack_bot_token_active == ""
    monkeypatch.setattr(settings, "slack_bot_token", "  ")
    assert settings.slack_bot_token_active == ""
    monkeypatch.setattr(settings, "slack_bot_token", "xoxb-real")
    assert settings.slack_bot_token_active == "xoxb-real"


async def test_unconfigured_token_drops_the_mention_without_posting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "slack_bot_token", "")
    posted: list[str] = []

    async def _fake_post(*args: Any) -> None:
        posted.append(args[3])

    monkeypatch.setattr(integrations, "_post_thread", _fake_post)
    await integrations._deliver_mention("C1", "1.0", "1.0", "<@UBOT> hi", {})
    assert posted == []  # inert by design until the token secret is filled


async def test_thread_context_pages_through_to_the_newest_replies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A long thread's freshest replies survive; the oldest page gets trimmed."""
    pages = [
        {
            "ok": True,
            "messages": [
                {"user": f"U{i}", "ts": f"1.{i:04d}", "text": f"stale early chatter {i}" * 4}
                for i in range(200)
            ],
            "has_more": True,
            "response_metadata": {"next_cursor": "cursor-2"},
        },
        {
            "ok": True,
            "messages": [{"user": "U9", "ts": "2.0001", "text": "the freshest reply"}],
            "has_more": False,
        },
    ]
    calls: list[dict[str, Any]] = []

    class _FakeResp:
        def __init__(self, payload: dict[str, Any]) -> None:
            self._payload = payload

        def json(self) -> dict[str, Any]:
            return self._payload

    class _FakeClient:
        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def get(self, url: str, params: dict[str, Any], headers: dict[str, str]) -> _FakeResp:
            calls.append(params)
            return _FakeResp(pages[1] if params.get("cursor") else pages[0])

    monkeypatch.setattr("app.routers.integrations.httpx.AsyncClient", lambda **_kw: _FakeClient())
    context = await integrations._thread_context("xoxb-test", "C1", "1.0000", "9.9999")
    assert [p.get("cursor") for p in calls] == [None, "cursor-2"]
    assert "the freshest reply" in context
    assert len(context) <= integrations.THREAD_CONTEXT_MAX_CHARS


async def test_mention_acks_before_answering_and_still_reports_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The asker hears "working on it" immediately, then the outcome — in order."""
    monkeypatch.setattr(settings, "slack_bot_token", "xoxb-test")
    timeline: list[tuple[str, str]] = []

    async def _fake_ephemeral(_t: str, _c: str, user_id: str, _ts: str, text: str) -> None:
        timeline.append(("ack:" + user_id, text))

    async def _fake_thread(_t: str, _c: str, _ts: str, text: str) -> None:
        timeline.append(("thread", text))

    async def _no_account() -> None:
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="No active Slack service account")

    monkeypatch.setattr(integrations, "_post_ephemeral", _fake_ephemeral)
    monkeypatch.setattr(integrations, "_post_thread", _fake_thread)
    monkeypatch.setattr(integrations, "_slack_service_account", _no_account)

    await integrations._deliver_mention(
        "C1", "1.0", "1.0", "<@UBOT> median rent", {"user_id": "U123"}
    )
    assert [kind for kind, _ in timeline] == ["ack:U123", "thread"]
    assert "Working on it" in timeline[0][1]
    assert "median rent" in timeline[0][1]
    assert "Couldn't answer that" in timeline[1][1]
