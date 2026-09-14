"""Self-hosted OTLP export for the data-agent (s37).

Logfire is an OpenTelemetry SDK, so the pydantic-ai and httpx instrumentation
this service already carries emits ordinary OTel spans — only the destination is
a choice. Setting ``OTLP_ENDPOINT`` adds an exporter pointed at a collector you
run; locally that is the Jaeger container in docker-compose.

Deliberately standalone, importing nothing from this package: ``main`` has to
call ``logfire.configure()`` before it imports ``agent.config`` (agent_common
instruments pydantic-ai at import time and needs configure to have run first),
so this reads the environment directly rather than forcing that ordering to
change for a telemetry setting.
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import Iterator
from typing import Any

log = logging.getLogger(__name__)

_TRACER_NAME = "data-agent.sdk_agent"


def otlp_processors() -> list[Any]:
    """A span processor per configured OTLP endpoint — empty when unset.

    HTTP rather than gRPC: logfire already ships the proto-http exporter, so
    this needs no new dependency, and MLflow's OTLP ingest lives at
    ``/v1/traces``.

    Additive, not exclusive. With both a Logfire token and an OTLP endpoint set,
    spans go to both — which makes swapping backends a side-by-side comparison
    rather than a cutover.
    """
    endpoint = os.environ.get("OTLP_ENDPOINT", "").strip()
    if not endpoint:
        return []
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:  # pragma: no cover — ships with logfire
        log.warning("OTLP exporter unavailable; self-hosted tracing disabled")
        return []
    # s43 M1: MLflow's OTLP endpoint routes spans to an experiment via this
    # header (required by its /v1/traces ingest, MLflow >= 3.6). Empty = plain
    # OTLP with no extra header, which is what Jaeger-style collectors expect.
    experiment_id = os.environ.get("MLFLOW_TRACE_EXPERIMENT_ID", "").strip()
    headers = {"x-mlflow-experiment-id": experiment_id} if experiment_id else None
    return [
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces", headers=headers)
        )
    ]


class _NoOpSpan:
    """What :func:`agent_span` yields when OTLP export is not configured.

    Same call surface as a real OTel ``Span`` for the two methods callers use,
    so ``sdk_agent.py`` never needs an ``if OTLP_ENDPOINT`` branch of its own —
    it just calls ``span.set_attribute(...)`` unconditionally.
    """

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def record_exception(self, exc: BaseException) -> None:
        pass


@contextlib.contextmanager
def agent_span(name: str, **attributes: Any) -> Iterator[Any]:
    """A manual OTel span for a run of the Claude Agent SDK runtime (s44 M3b).

    The pydantic-ai champion gets its spans for free — logfire's pydantic-ai
    instrumentation wraps every model/tool call automatically. The Agent SDK
    runtime drives an external CLI subprocess that nothing auto-instruments,
    so ``sdk_agent.answer_with_sdk`` opens one of these by hand around a run,
    the way ``otlp_processors()`` is the manual half of this service's
    exporter wiring.

    A genuine no-op — no tracer looked up, no span created — when
    ``OTLP_ENDPOINT`` is unset, mirroring :func:`otlp_processors`'s own check
    exactly: "no destination configured" behaves identically for the exporter
    and for this. Keyword attributes with a ``None`` value are dropped rather
    than stringified (OTel span attributes don't accept ``None``); the span
    object is yielded either way so the caller can add more attributes once
    the run's outcome is known.
    """
    endpoint = os.environ.get("OTLP_ENDPOINT", "").strip()
    if not endpoint:
        yield _NoOpSpan()
        return
    try:
        from opentelemetry import trace
    except ImportError:  # pragma: no cover — ships with logfire
        yield _NoOpSpan()
        return
    tracer = trace.get_tracer(_TRACER_NAME)
    with tracer.start_as_current_span(name) as span:
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(key, value)
        yield span


def clip(value: Any, limit: int) -> str:
    """``value`` as a span-safe string, truncated to ``limit`` chars.

    Span attributes are shipped on every export, so an untruncated 200 KB SQL
    string or traceback would dominate the payload and, on some collectors, be
    dropped outright. The marker keeps a reader from mistaking a cut string for
    the whole thing.
    """
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + f"…[+{len(text) - limit} chars]"


def current_context() -> Any:
    """The ambient OTel context, or ``None`` when tracing is off.

    Captured inside :func:`agent_span` by the caller and handed to
    :func:`child_span` so a span opened from a *different* asyncio task — an
    MCP tool handler the Agent SDK invokes, a PreToolUse hook — still nests
    under the run's span. Relying on the ambient contextvar alone would work
    only for as long as those callbacks happen to run on a task descended from
    the one that opened the outer span, which is the SDK's business, not ours.
    """
    if not os.environ.get("OTLP_ENDPOINT", "").strip():
        return None
    try:
        from opentelemetry import context as otel_context
    except ImportError:  # pragma: no cover — ships with logfire
        return None
    return otel_context.get_current()


@contextlib.contextmanager
def child_span(name: str, *, parent: Any = None, **attributes: Any) -> Iterator[Any]:
    """A child span under ``parent`` (or the ambient context) — s49 M0.

    Same no-op contract as :func:`agent_span`: nothing is looked up or created
    when ``OTLP_ENDPOINT`` is unset, and ``None`` attributes are dropped rather
    than stringified. ``parent`` is an opaque OTel ``Context`` from
    :func:`current_context`; passing ``None`` nests under whatever is current.
    """
    if not os.environ.get("OTLP_ENDPOINT", "").strip():
        yield _NoOpSpan()
        return
    try:
        from opentelemetry import trace
    except ImportError:  # pragma: no cover — ships with logfire
        yield _NoOpSpan()
        return
    tracer = trace.get_tracer(_TRACER_NAME)
    with tracer.start_as_current_span(name, context=parent) as span:
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(key, value)
        yield span


def emit_span(name: str, *, parent: Any = None, **attributes: Any) -> None:
    """A point-in-time child span: everything about it is known up front.

    For steps whose duration this process cannot measure honestly — a model
    turn (the SDK reports it after the fact) or a built-in file tool the CLI
    runs itself — recording a zero-width span with the right attributes is more
    truthful than inventing a start time.
    """
    with child_span(name, parent=parent, **attributes):
        pass


def set_attributes(span: Any, **attributes: Any) -> None:
    """Add attributes to an already-open span, dropping the ``None`` ones.

    The counterpart to :func:`child_span`'s keyword attributes for everything a
    span only learns from its own outcome — a row count, a status, an elapsed
    time. ``_NoOpSpan`` absorbs the call, so this is free with tracing off.
    """
    for key, value in attributes.items():
        if value is not None:
            span.set_attribute(key, value)
