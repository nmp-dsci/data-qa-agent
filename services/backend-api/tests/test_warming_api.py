"""s29: the db_warming API contract (service half — needs the fastapi stack).

Pins what the frontend login retry loop depends on: a waking-classified failure
surfaces as 503 {"detail": "db_warming"} with Retry-After and CORS headers, a
real error stays a CORS-carrying 500, and /health/db answers "waking" instead
of erroring while the database resumes — but only probes (and so wakes) the
database for requests carrying the app's channel marker, so an external poller
cannot hold Aurora awake. Pure handler-level tests — no server, no database.
The classifier itself is covered in the repo-root suite
(tests/test_db_warming.py).
"""

from __future__ import annotations

import asyncio
import json

import pytest
from starlette.requests import Request

from app import main as backend_main
from app.config import settings


def _request(origin: str | None = None, channel: str | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if origin:
        headers.append((b"origin", origin.encode()))
    if channel:
        headers.append((b"x-client-channel", channel.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/me",
            "headers": headers,
            "query_string": b"",
            "scheme": "http",
            "server": ("test", 80),
        }
    )


def _allowed_origin() -> str:
    return settings.all_cors_origins[0]


def test_waking_error_returns_retryable_503_with_cors() -> None:
    origin = _allowed_origin()
    resp = asyncio.run(backend_main._unhandled_error(_request(origin), ConnectionRefusedError()))
    assert resp.status_code == 503
    assert json.loads(bytes(resp.body)) == {"detail": "db_warming"}
    assert resp.headers["Retry-After"] == "5"
    assert resp.headers["Access-Control-Allow-Origin"] == origin


def test_real_error_stays_a_500_with_cors() -> None:
    resp = asyncio.run(backend_main._unhandled_error(_request(_allowed_origin()), ValueError()))
    assert resp.status_code == 500
    assert json.loads(bytes(resp.body)) == {"detail": "Internal server error"}
    assert "Retry-After" not in resp.headers
    assert resp.headers["Access-Control-Allow-Origin"] == _allowed_origin()


def test_unknown_origin_gets_no_cors_headers() -> None:
    resp = asyncio.run(
        backend_main._unhandled_error(_request("https://evil.example"), ValueError())
    )
    assert "Access-Control-Allow-Origin" not in resp.headers


class _RefusingEngine:
    def connect(self) -> None:  # duck-typed stand-in for AsyncEngine
        raise ConnectionRefusedError(61, "Connection refused")


class _BrokenEngine:
    def connect(self) -> None:
        raise ValueError("not a connectivity problem")


@pytest.fixture(autouse=True)
def _reset_health_db_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backend_main, "_health_db_cache", None)
    monkeypatch.setattr(backend_main, "_health_db_cache_at", 0.0)


def test_health_db_reports_waking_when_connect_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backend_main, "engine", _RefusingEngine())
    result = asyncio.run(backend_main.health_db(_request(channel="web")))
    assert result["status"] == "waking"


def test_health_db_raises_on_non_connectivity_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backend_main, "engine", _BrokenEngine())
    with pytest.raises(ValueError):
        asyncio.run(backend_main.health_db(_request(channel="web")))


def test_health_db_skips_the_probe_without_the_channel_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # _BrokenEngine raises on any connect, so a "skipped" answer proves the
    # unmarked request never touched (woke) the database.
    monkeypatch.setattr(backend_main, "engine", _BrokenEngine())
    assert asyncio.run(backend_main.health_db(_request()))["status"] == "skipped"
    assert asyncio.run(backend_main.health_db(_request(channel="api")))["status"] == "skipped"


def test_health_db_coalesces_repeated_marked_probes_within_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backend_main, "engine", _RefusingEngine())
    first = asyncio.run(backend_main.health_db(_request(channel="web")))
    assert first["status"] == "waking"

    monkeypatch.setattr(backend_main, "engine", _BrokenEngine())
    second = asyncio.run(backend_main.health_db(_request(channel="web")))
    assert second == first
