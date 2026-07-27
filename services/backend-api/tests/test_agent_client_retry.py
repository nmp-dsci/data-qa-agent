"""Hop-retry classification for the backend->agent call (s32 W1 follow-up).

The agent already retries slow calls internally, up to 4 attempts
(agent/model_factory.py). A read/write/pool timeout on the backend's side of
the hop means the request reached the agent and that internal retry policy
may already be mid-flight, so retrying here too would restart the whole
answer and stack ~3x latency/spend on one legitimately slow question. Only a
failure to establish the connection at all is worth another attempt here.
"""

from __future__ import annotations

import httpx
import pytest

from app.agent_client import _is_retryable_hop


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://agent.example/agent/ask")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ReadTimeout("timed out"),
        httpx.WriteTimeout("timed out"),
        httpx.PoolTimeout("timed out"),
    ],
)
def test_read_write_pool_timeout_is_not_retried(exc: httpx.TransportError) -> None:
    assert _is_retryable_hop(exc) is False


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("refused"),
        httpx.ConnectTimeout("timed out"),
    ],
)
def test_connection_establishment_failure_is_retried(exc: httpx.TransportError) -> None:
    assert _is_retryable_hop(exc) is True


def test_retryable_status_codes_are_retried() -> None:
    for code in (429, 500, 502, 503, 504):
        assert _is_retryable_hop(_status_error(code)) is True


def test_client_error_status_is_not_retried() -> None:
    assert _is_retryable_hop(_status_error(400)) is False
