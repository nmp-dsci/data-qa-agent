"""Head sampler that drops health-check and scrape noise before export (s50).

Logfire instruments every FastAPI request, and the OTLP exporter shipped all of
them: on the local stack the ``data-qa/traces`` experiment filled with ~140k
``GET /metrics`` (Prometheus scrape) and ~140k ``GET /health`` (docker
healthcheck) traces around a few dozen ``POST /ask`` — the traces anyone
actually opens MLflow to read were unfindable.

A *head* sampler is the right fix rather than a filtering exporter: the
decision is made when the root span starts, so the whole trace (root and every
child, in this process or a downstream one that inherits the ``traceparent``)
is never recorded at all. ``ParentBased`` wraps it so only *root* spans are
judged by name — a child span called ``GET /health`` inside a kept trace stays,
and a child of a dropped root is dropped with it, even across the
backend-api → data-agent hop.

Pure functions so the name → decision table is unit-testable without an SDK
tracer; :func:`noise_sampler` builds the OTel object lazily so this module is
importable when OpenTelemetry is absent.
"""

from __future__ import annotations

import re
from typing import Any

# Root span names that are never worth a trace. Anchored on the full name,
# case-sensitive, because the instrumentation emits ``METHOD /route`` exactly.
#
# * ``GET /metrics`` — Prometheus scrape.
# * ``GET /health`` and ``GET /health/...`` — docker/App Runner healthchecks
#   and the login card's DB wake probe.
# * ``OPTIONS ...`` — CORS preflight; the real request follows as its own trace.
# * ``POST /events`` — the frontend product-analytics beacon (``routers/events``),
#   NOT a chat stream: fires on every page view and carries no agent work.
NOISY_ROOT_SPANS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^GET /metrics$"),
    re.compile(r"^GET /health(/.*)?$"),
    re.compile(r"^OPTIONS( .*)?$"),
    re.compile(r"^(GET|POST) /events$"),
)


def is_noisy_root(name: str) -> bool:
    """True when a root span with this name should not be traced at all."""
    return any(p.match(name) for p in NOISY_ROOT_SPANS)


def noise_sampler() -> Any:
    """A ``ParentBased(root=<name filter>)`` OTel sampler, for ``SamplingOptions(head=...)``.

    Root spans are kept unless :func:`is_noisy_root` says otherwise; children
    follow their parent's decision (local or remote) exactly as the default
    ``ParentBased(ALWAYS_ON)`` would, so context propagation is unchanged.
    """
    from opentelemetry.sdk.trace.sampling import (
        Decision,
        ParentBased,
        Sampler,
        SamplingResult,
    )

    class _RootNameSampler(Sampler):
        def should_sample(
            self,
            parent_context: Any,
            trace_id: int,
            name: str,
            kind: Any = None,
            attributes: Any = None,
            links: Any = None,
            trace_state: Any = None,
        ) -> SamplingResult:
            if is_noisy_root(name):
                return SamplingResult(Decision.DROP, None, trace_state)
            return SamplingResult(Decision.RECORD_AND_SAMPLE, attributes, trace_state)

        def get_description(self) -> str:
            return "RootNameSampler{drop health/metrics/preflight/analytics roots}"

    return ParentBased(root=_RootNameSampler())
