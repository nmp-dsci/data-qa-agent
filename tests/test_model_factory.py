"""The retry policy every LLM call now goes through (s32 W1).

Worth testing at the root, in the dependency-light venv, for the same reason the
module avoids importing pydantic-ai at runtime: the policy that governs six call
sites should be verifiable without an LLM stack, and the classification rules are
the part that would silently rot.

The two rules that actually matter, and are easy to get backwards:

* **Never retry a 4xx.** A 400 or a 401 will fail identically three more times,
  and each attempt costs money on some providers.
* **Never retry our own runaway guard.** ``UsageLimitExceeded`` exists to stop a
  misbehaving model burning tokens; a retry loop around it does the opposite of
  what it was built for — and it stays terminal even when wrapped in something
  that looks retryable.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "data-agent"))

from agent.model_factory import (  # noqa: E402
    DEFAULT_POLICY,
    FAST_POLICY,
    RetryPolicy,
    backoff_delay,
    build_model,
    is_retryable,
    model_settings,
    run_with_policy,
)

# --- stand-ins for the provider exceptions, matched by name, not by import ---


class ModelHTTPError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"http {status_code}")
        self.status_code = status_code


class ConnectError(Exception):
    """Named like httpx's, which is all the classifier looks at."""


class UsageLimitExceeded(Exception):
    """Named like pydantic-ai's own runaway guard."""


class BadRequestError(Exception):
    pass


def test_server_and_rate_limit_statuses_retry() -> None:
    for status in (429, 500, 502, 503, 504, 529, 408):
        assert is_retryable(ModelHTTPError(status)), status


def test_client_errors_never_retry() -> None:
    # The money rule: a malformed or unauthorised request fails the same way
    # every time, so a retry is pure spend.
    for status in (400, 401, 403, 404, 422):
        assert not is_retryable(ModelHTTPError(status)), status


def test_transport_failures_retry() -> None:
    assert is_retryable(ConnectError("connection reset"))
    assert is_retryable(TimeoutError("read timed out"))


def test_usage_limit_is_terminal_even_when_wrapped() -> None:
    # The guard that caps a runaway run must survive being wrapped in something
    # that looks retryable, or the retry loop defeats it.
    wrapped = ConnectError("transport blew up")
    wrapped.__cause__ = UsageLimitExceeded("request limit exceeded")
    assert not is_retryable(wrapped)


def test_status_beats_a_retryable_looking_name() -> None:
    # A 4xx wrapped in a transport-shaped error is still a 4xx.
    outer = ConnectError("connection reset")
    outer.__cause__ = ModelHTTPError(401)
    assert not is_retryable(outer)


def test_unknown_exceptions_do_not_retry() -> None:
    # Default deny: an unrecognised failure is more likely a bug in our code
    # than a flaky network, and retrying a bug just triples the log noise.
    assert not is_retryable(ValueError("bad column"))
    assert not is_retryable(BadRequestError("nope"))


def test_self_referential_cause_chain_terminates() -> None:
    a = ConnectError("a")
    b = ConnectError("b")
    a.__cause__ = b
    b.__cause__ = a
    assert is_retryable(a)  # and, crucially, returns at all


def test_backoff_is_bounded_and_jittered() -> None:
    policy = RetryPolicy(base_delay_s=1.0, max_delay_s=20.0)
    for attempt in range(1, 10):
        delay = backoff_delay(attempt, policy)
        assert 0.0 <= delay <= policy.max_delay_s
    # The ceiling grows with the attempt but never past max_delay_s — the point
    # of the cap is that a real outage fails fast rather than slowly.
    assert backoff_delay(1, RetryPolicy(base_delay_s=1.0, max_delay_s=20.0)) <= 1.0


def test_policies_bound_worst_case_wait() -> None:
    # The plan's promise: 4 attempts, 20s jitter ceiling, then degrade. Bounding
    # the worst case is what makes the degraded path fast rather than slow.
    worst = sum(
        min(DEFAULT_POLICY.base_delay_s * 2 ** (a - 1), DEFAULT_POLICY.max_delay_s)
        for a in range(1, DEFAULT_POLICY.attempts)
    )
    assert worst <= 30.0
    assert FAST_POLICY.timeout_s < DEFAULT_POLICY.timeout_s


def test_build_model_and_settings() -> None:
    assert build_model("deepseek", "deepseek-chat") == "deepseek:deepseek-chat"
    assert model_settings(RetryPolicy(timeout_s=12.0))["timeout"] == 12.0


# --- the loop itself --------------------------------------------------------


async def _noop_sleep(_seconds: float) -> None:
    """Skip the real backoff so the tests don't wait for it."""


def test_succeeds_first_try_reports_one_attempt() -> None:
    async def run() -> str:
        return "ok"

    result = asyncio.run(run_with_policy(run, sleep=_noop_sleep))
    assert result == ("ok", 1)


def test_retries_then_succeeds_and_counts_attempts() -> None:
    calls: list[int] = []

    async def flaky() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise ModelHTTPError(429)
        return "eventually"

    value, attempts = asyncio.run(run_with_policy(flaky, sleep=_noop_sleep))
    assert value == "eventually"
    # The number that lands in query_runs.attempts, which is what makes "the
    # provider was flaky but the user never noticed" a visible metric.
    assert attempts == 3


def test_gives_up_after_the_attempt_budget() -> None:
    calls: list[int] = []

    async def always_429() -> str:
        calls.append(1)
        raise ModelHTTPError(429)

    with pytest.raises(ModelHTTPError):
        asyncio.run(run_with_policy(always_429, policy=RetryPolicy(attempts=3), sleep=_noop_sleep))
    assert len(calls) == 3


def test_a_4xx_is_raised_on_the_first_attempt() -> None:
    calls: list[int] = []

    async def bad_request() -> str:
        calls.append(1)
        raise ModelHTTPError(400)

    with pytest.raises(ModelHTTPError):
        asyncio.run(run_with_policy(bad_request, sleep=_noop_sleep))
    assert len(calls) == 1


def test_should_retry_veto_stops_a_replay() -> None:
    """The multi-turn case: a late failure must not replay paid-for work."""
    calls: list[int] = []
    produced = {"report": False}

    async def fails_after_producing() -> str:
        calls.append(1)
        produced["report"] = True  # the run got somewhere before dying
        raise ModelHTTPError(503)

    with pytest.raises(ModelHTTPError):
        asyncio.run(
            run_with_policy(
                fails_after_producing,
                should_retry=lambda: not produced["report"],
                sleep=_noop_sleep,
            )
        )
    assert len(calls) == 1
