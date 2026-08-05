"""Tracing for backend-api (s32 W2) — configure it, and read the current id.

Before this, only the data-agent was instrumented, so a chat request produced a
trace of *half* the work: the model turns were visible and the auth, RLS, and
persistence around them were not. Worse, ``LOGFIRE_TOKEN`` was never wired into
Terraform, so prod shipped no traces at all — the app ran blind exactly where
blindness costs the most.

Three pieces:

* :func:`configure` — ``logfire.configure`` plus FastAPI and httpx
  instrumentation, called from ``main`` before the routers import. With no token
  it configures to local-only, exactly like the agent, so importing this module
  is always safe.
* :func:`current_trace_id` — the active trace id as the 32-hex string Logfire's
  UI searches on, stamped onto ``app.query_runs.otel_trace_id`` so a slow row on
  the ops deck is one lookup from its span waterfall.
* :func:`RequestIdMiddleware` — a request id on every response, so a user can
  quote one line from their browser's network tab and it is findable in the logs
  whether or not tracing is switched on.

Every function here degrades to a no-op if OpenTelemetry or Logfire is missing,
because observability failing must never take the service with it.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .config import settings

log = logging.getLogger("uvicorn.error")

REQUEST_ID_HEADER = "X-Request-ID"


_configured = False


def configure() -> None:
    """Configure Logfire/OTel for this service; safe to call when either is absent.

    ``send_to_logfire="if-token-present"`` is the same setting the data-agent
    uses: with a token, spans ship; without one, the SDK stays local. Scrubbing
    is left at Logfire's default (decision Q4) — its built-in scrubber plus the
    regex pass in ``app.scrub`` is the right weight for data that is public NSW
    property records with a free-text question as the only PII surface.

    Idempotent: ``main`` calls this at import time (so the httpx patch is in
    place before ``agent_client`` is imported) and the guard makes a second call
    a no-op.
    """
    global _configured
    if _configured:
        return
    _configured = True
    try:
        import logfire
    except ImportError:  # pragma: no cover — logfire is a declared dependency
        log.info("logfire not installed; backend tracing disabled")
        return
    try:
        logfire.configure(
            service_name="backend-api",
            send_to_logfire="if-token-present",
            additional_span_processors=_otlp_processors(),
        )
        # capture_all is deliberately off: the outbound calls this service makes
        # carry whole questions and answers, and a span attribute is a copy of
        # that payload outside the database.
        logfire.instrument_httpx()
    except Exception as exc:  # noqa: BLE001 — never fail startup over telemetry
        log.warning("backend tracing setup skipped: %s", exc)


def _otlp_processors() -> list[Any]:
    """Export to a self-hosted OTLP collector when one is configured (s37).

    Logfire is an OpenTelemetry SDK, so the instrumentation is backend-agnostic:
    the FastAPI/httpx/pydantic-ai spans are ordinary OTel spans and only their
    destination is a choice. Setting ``OTLP_ENDPOINT`` adds an exporter pointed
    at whatever you run — locally that is the Jaeger container in
    docker-compose. Unset, this returns nothing and behaviour is exactly as
    before.

    HTTP rather than gRPC on purpose: logfire already ships the
    proto-http exporter, so this needs no new dependency, and Jaeger accepts
    OTLP/HTTP on 4318.

    This is additive, not exclusive — with both a Logfire token and an OTLP
    endpoint set, spans go to both. That makes switching backends a
    side-by-side comparison rather than a cutover.
    """
    endpoint = settings.otlp_endpoint.strip()
    if not endpoint:
        return []
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:  # pragma: no cover — ships with logfire
        log.warning("OTLP exporter unavailable; self-hosted tracing disabled")
        return []
    log.info("exporting traces to %s", endpoint)
    return [BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces"))]


def instrument_app(app: FastAPI) -> None:
    """Instrument the FastAPI app once its routes are registered.

    Separate from :func:`configure` because the route table has to exist first —
    instrumenting an empty app produces spans labelled with raw paths instead of
    route templates, which makes per-endpoint latency unaggregatable.
    """
    try:
        import logfire

        logfire.instrument_fastapi(app)
    except Exception as exc:  # noqa: BLE001 — never fail startup over telemetry
        log.warning("backend FastAPI instrumentation skipped: %s", exc)


def current_trace_id() -> str | None:
    """The active trace id as 32 lowercase hex chars, or None when untraced.

    Returns the *trace* id rather than the span id because that is what the
    Logfire UI searches on, and what makes one id cover the whole request
    including the agent hop it propagated to.
    """
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        context = span.get_span_context()
        if not context or not context.trace_id:
            return None
        return format(context.trace_id, "032x")
    except Exception:  # noqa: BLE001 — an untraced request is normal, not an error
        return None


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a request id to every response, echoing the caller's if it sent one.

    Independent of tracing on purpose: this is the id a user can read off a
    failed request and quote, and it has to exist even in a deployment with no
    ``LOGFIRE_TOKEN`` set. When tracing *is* on, the trace id is used as the
    request id so the two identifiers are the same string rather than two things
    to correlate by hand.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = incoming or current_trace_id() or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


def logfire_configured() -> bool:
    """Whether a token is set — reported on the admin config page, never the value."""
    return bool(settings.logfire_token)
