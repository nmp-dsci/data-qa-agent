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
