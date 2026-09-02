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

import logging
import os
from typing import Any

log = logging.getLogger(__name__)


def otlp_processors() -> list[Any]:
    """A span processor per configured OTLP endpoint — empty when unset.

    HTTP rather than gRPC: logfire already ships the proto-http exporter, so
    this needs no new dependency, and Jaeger accepts OTLP/HTTP on 4318.

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
