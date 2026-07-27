"""One retry/timeout policy for every model call (s32 W1).

The audit that opened this workstream found the same three lines copy-pasted at
six call sites::

    agent = Agent(f"{provider}:{model_name}", ...)
    run = await agent.run(instruction)

No timeout, no retry, no backoff — pydantic-ai's library defaults, which means a
single provider 429 or a dropped connection surfaced as a failed answer. This
module is the one place that policy now lives, and every site goes through it.

Two functions, deliberately small:

* :func:`build_model` — the ``"provider:model"`` spec plus the ``ModelSettings``
  that carry the per-request timeout. Nothing clever; it exists so a timeout
  can't be forgotten at a new call site.
* :func:`run_with_policy` — wraps one ``agent.run(...)`` in bounded, jittered
  exponential backoff and reports how many attempts it took.

**Retry on transport and server, never on 4xx.** A 429 or a 503 means "ask
again"; a 400 or a 401 means the request is wrong and asking again just spends
money. The classifier walks the exception chain (pydantic-ai wraps provider
errors) and decides by *name and attributes*, not by importing provider SDKs —
which keeps this module importable from the dependency-light root test venv, so
the policy that governs every LLM call is unit-testable without an LLM stack.
Same trick, same reason as ``waking.is_db_waking`` on the backend.

**Bounded, then degrade.** Four attempts with a 20s jitter ceiling, then the
caller gives up and returns a degraded answer. The degraded path is
deterministic (the offline stub), so the worst case under a real provider
outage is *fast*, not slow — a retry policy that turns a 30s failure into a
four-minute one has made things worse.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    # Type-only: `from __future__ import annotations` keeps this out of the
    # runtime import graph, so the module still imports in the dependency-light
    # root test venv where pydantic-ai isn't installed.
    from pydantic_ai.settings import ModelSettings

# HTTP statuses worth asking again about: rate limits, overload, and the 5xx
# family. 529 is Anthropic's "overloaded"; 425/408/409 are transport-ish.
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})

# Exception *names* (own class or any base) that mean "transport or upstream
# hiccup". Matched by name so no provider SDK has to be importable here.
_RETRYABLE_NAMES = frozenset(
    {
        # pydantic-ai
        "ModelAPIError",
        "ModelHTTPError",
        # httpx
        "TimeoutException",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "ConnectError",
        "ReadError",
        "WriteError",
        "NetworkError",
        "TransportError",
        "RemoteProtocolError",
        "ProxyError",
        # openai / anthropic SDKs
        "APIConnectionError",
        "APITimeoutError",
        "InternalServerError",
        "RateLimitError",
        "ServiceUnavailableError",
        "OverloadedError",
        # stdlib
        "TimeoutError",
        "ConnectionError",
        "ConnectionResetError",
    }
)

# Checked FIRST, so a subclass that also matches above still loses. These are
# "the request or the run is wrong" — retrying cannot help and costs money.
_TERMINAL_NAMES = frozenset(
    {
        "UsageLimitExceeded",  # our own runaway guard — retrying defeats it
        "UnexpectedModelBehavior",
        "UserError",
        "ModelRetry",  # a tool-level retry, owned by pydantic-ai's own loop
        "ContentFilterError",
        "ValidationError",
        "AuthenticationError",
        "PermissionDeniedError",
        "BadRequestError",
        "NotFoundError",
        "UnprocessableEntityError",
        "CancelledError",  # the caller aborted; never fight that
    }
)


@dataclass(frozen=True)
class RetryPolicy:
    """How hard to try, and how long to wait between tries.

    ``attempts`` counts the first try, so 4 means "one attempt plus three
    retries". ``timeout_s`` bounds a single model request, not the whole run —
    a full report legitimately takes minutes across many turns.
    """

    attempts: int = 4
    timeout_s: float = 90.0
    base_delay_s: float = 1.0
    max_delay_s: float = 20.0


# The default every site inherits. A short-call site (titling) overrides the
# timeout rather than the shape.
DEFAULT_POLICY = RetryPolicy()
# Cheap, latency-sensitive calls that must never hold an answer open: the
# conversation titler and the SQL-assist round trip.
FAST_POLICY = RetryPolicy(attempts=3, timeout_s=25.0, max_delay_s=6.0)


class Attempted(NamedTuple):
    """A model call's result plus how many attempts it actually took.

    ``attempts`` is what lands in ``app.query_runs.attempts``, which is what
    makes "the provider was flaky but the user never saw it" a visible number
    on the ops deck instead of an invisible cost.
    """

    value: Any
    attempts: int


def _causes(exc: BaseException) -> list[BaseException]:
    """The exception and its ``__cause__``/``__context__`` chain, depth-bounded.

    pydantic-ai wraps provider errors, and provider SDKs wrap httpx errors, so
    the retryable signal is usually two or three links down. Bounded so a
    self-referential chain can't spin.
    """
    seen: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and len(seen) < 8:
        if any(current is s for s in seen):
            break
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


def _names(exc: BaseException) -> set[str]:
    return {klass.__name__ for klass in type(exc).__mro__}


def is_retryable(exc: BaseException) -> bool:
    """Should this failure be retried?

    Order matters: a terminal marker anywhere in the chain wins, because
    "usage limit exceeded" wrapped in a transport error is still a usage limit.
    An explicit non-retryable HTTP status also wins — that is the "never on 4xx"
    rule, and it beats a retryable-looking class name.
    """
    chain = _causes(exc)
    for link in chain:
        if _names(link) & _TERMINAL_NAMES:
            return False
    for link in chain:
        status = getattr(link, "status_code", None)
        if isinstance(status, int):
            return status in RETRYABLE_STATUS
    return any(_names(link) & _RETRYABLE_NAMES for link in chain)


def backoff_delay(attempt: int, policy: RetryPolicy = DEFAULT_POLICY) -> float:
    """Jittered exponential backoff for ``attempt`` (1-based), capped.

    Full jitter — a uniform draw from ``[0, exponential]`` rather than
    ``exponential ± noise``. With one client this hardly matters; it matters the
    moment two requests fail in the same second, which is exactly what a
    provider rate limit produces.
    """
    ceiling = min(policy.base_delay_s * (2 ** max(0, attempt - 1)), policy.max_delay_s)
    return random.uniform(0, ceiling)


def build_model(provider: str, model_name: str) -> str:
    """The pydantic-ai model spec for a provider/model pair.

    Trivial today, and that is fine: having every site call it means a change
    of addressing scheme (a gateway prefix, a region) is one edit, not six.
    """
    return f"{provider}:{model_name}"


def model_settings(policy: RetryPolicy = DEFAULT_POLICY) -> ModelSettings:
    """``ModelSettings`` carrying the per-request timeout.

    ``ModelSettings`` is a ``TypedDict``, so the returned dict literal *is* one —
    which is why the import above can stay type-only and this module needs no
    pydantic-ai at runtime.
    """
    return {"timeout": policy.timeout_s}


async def run_with_policy[T](
    run: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy = DEFAULT_POLICY,
    label: str = "model",
    should_retry: Callable[[], bool] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Attempted:
    """Run ``run()`` under the retry policy; raise the last error if all fail.

    ``run`` is a zero-argument coroutine factory (usually
    ``lambda: agent.run(...)``) so each attempt builds a fresh awaitable — an
    already-awaited coroutine cannot be retried.

    ``should_retry`` is the caller's veto, consulted *after* the failure is
    classified as retryable. A single-shot call (titling, SQL assist) never
    needs it; a multi-turn agent run does, because replaying a run that already
    made ten tool calls pays for all of them twice. The sandbox site passes
    "only if nothing was produced yet", so a late failure is salvaged instead of
    replayed.

    ``sleep`` is injectable purely so the backoff is testable without a test
    that actually waits 20 seconds.
    """
    attempts = max(1, policy.attempts)
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            value = await run()
        except Exception as exc:  # noqa: BLE001 — classified immediately below
            last = exc
            if attempt >= attempts or not is_retryable(exc):
                raise
            if should_retry is not None and not should_retry():
                print(
                    f"[data-agent] {label} attempt {attempt} failed "
                    f"({type(exc).__name__}) but partial work exists; not replaying"
                )
                raise
            delay = backoff_delay(attempt, policy)
            print(
                f"[data-agent] {label} attempt {attempt}/{attempts} failed "
                f"({type(exc).__name__}: {str(exc)[:120]}); retrying in {delay:.1f}s"
            )
            await sleep(delay)
        else:
            return Attempted(value, attempt)
    # Unreachable: the loop either returns or re-raises on its final attempt.
    raise last if last is not None else RuntimeError(f"{label}: no attempt ran")
