from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import text

from .tracing import RequestIdMiddleware, instrument_app
from .tracing import configure as configure_tracing

# Tracing is configured before anything else in this package is imported (s32
# W2), the same ordering rule the data-agent follows for its pydantic-ai
# instrumentation: logfire.configure() has to run before any instrument_* call,
# and doing it at the very top means no module can accidentally get imported
# ahead of it. instrument_fastapi comes later, once the routes exist.
configure_tracing()

from . import demo_replay, metrics  # noqa: E402
from .config import settings  # noqa: E402 — after configure_tracing, by design
from .db import engine, rls_connection  # noqa: E402
from .explore.manifest import ManifestError, validate_manifest  # noqa: E402
from .mcp_surface import McpPathNormalizer, build_mcp_app  # noqa: E402
from .routers import (  # noqa: E402
    admin_config,
    admin_pack,
    analytics,
    architecture,
    ask,
    auth,
    evals,
    events,
    explore,
    feedback,
    goldens,
    integrations,
    ops,
    profile,
    service_accounts,
    sql,
)
from .waking import is_db_waking  # noqa: E402

log = logging.getLogger("uvicorn.error")

# Built at import time, before the lifespan runs: streamable_http_app() is what
# lazily creates the session manager, so the manager cannot be started until
# after this call has happened (s36).
_mcp_inner, mcp_gate = build_mcp_app()


# s38 P3: the demo janitor. Every visitor shares one demo user, so their chat
# residue accumulates; this clears conversations/messages older than a day.
# Events and query_runs are deliberately KEPT — they are the analytics tab's
# raw material (uniques, funnel, top questions) and are rate/size-capped at
# write time instead. In-process rather than an EventBridge/ECS job: demo
# deployments pin one instance, so a process task IS a singleton, and dev gets
# the same behaviour for free.
_DEMO_RESET_INTERVAL_S = 6 * 3600


async def _demo_reset_loop() -> None:
    while True:
        try:
            # RLS trap: an empty user context sees (and deletes) ZERO rows, so
            # the janitor must run AS the demo user — look the id up first
            # (app.users itself has no RLS) and delete inside that context.
            async with rls_connection(None) as conn:
                demo_id = (
                    await conn.execute(
                        text("SELECT id FROM app.users WHERE username = :u"),
                        {"u": settings.demo_username},
                    )
                ).scalar()
            if demo_id is not None:
                async with rls_connection(str(demo_id)) as conn:
                    await conn.execute(
                        text(
                            "DELETE FROM app.conversations WHERE user_id = :uid "
                            "AND created_at < now() - interval '24 hours'"
                        ),
                        {"uid": str(demo_id)},
                    )
        except Exception as exc:  # noqa: BLE001 — janitor failure must never kill the app
            log.warning("demo reset skipped: %s", exc)
        await asyncio.sleep(_DEMO_RESET_INTERVAL_S)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Explore manifest check: fail loudly if a declared dim/metric drifted from an
    # existing mart; tolerate marts that don't exist yet (pipeline still building
    # on first boot) with a warning, so the API can start ahead of the one-shot job.
    try:
        async with rls_connection(None) as conn:
            for warning in await validate_manifest(conn):
                log.warning("explore manifest: %s", warning)
    except ManifestError:
        raise
    except Exception as exc:  # noqa: BLE001 - DB not reachable yet; don't block startup
        log.warning("explore manifest validation skipped: %s", exc)
    # Starlette does NOT run a mounted app's lifespan, and the MCP transport's
    # session manager lives in exactly that lifespan — so mounting alone gives a
    # surface that imports fine, starts fine, and then fails on the first tool
    # call. Driving it from here is the whole cost of folding this service in.
    if settings.demo_mode:
        # Parse the pack once, off the event loop, before the first request
        # rather than lazily on it — load_pack() is lru_cache'd so this is the
        # only disk read that will ever happen, but doing it here means even
        # the very first /ask never pays that cost inline.
        await asyncio.to_thread(demo_replay.load_pack)
    reset_task = asyncio.create_task(_demo_reset_loop()) if settings.demo_mode else None
    # s40 M1 (D4): the Grafana queue-depth panel reads a gauge this poller
    # keeps fresh; scrapes then never block on Redis. Only runs in queue mode.
    if settings.queue_mode == "on":
        metrics.start_depth_poller()
    async with _mcp_inner.router.lifespan_context(_mcp_inner):
        yield
    if reset_task is not None:
        reset_task.cancel()
    await metrics.stop_depth_poller()
    await engine.dispose()


app = FastAPI(title="data-qa-agent API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.all_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# s32 W2: every response carries an id a user can quote, whether or not tracing
# is on. Added last, so it is the outermost layer and the header is set on every
# response including CORS preflights and error responses.
app.add_middleware(RequestIdMiddleware)
# s36: outermost, so /mcp is rewritten to the mount's path before routing ever
# reaches redirect_slashes. See McpPathNormalizer.
app.add_middleware(McpPathNormalizer)

# ---------------------------------------------------------------------------
# s38 P3: demo-mode guardrails.
#
# Concurrency gate — a global in-flight ceiling on the governed surfaces
# (ask / sql / explore / profile). The 21st concurrent request gets a clean
# 503 demo_full the frontend turns into "the demo is at capacity"; combined
# with App Runner max-instances=1 this makes the worst-case attack bill a
# fixed number instead of an autoscaling one. Health and static-config paths
# are exempt so monitoring never queues behind visitors.
#
# Origin cloaking — when ORIGIN_VERIFY_SECRET is set, any request that didn't
# carry the matching X-Origin-Verify header is refused, closing the public
# App Runner URL as a bypass route around a fronting CDN. NOT WIRED YET
# (ultrareview, s38): CloudFront in this stack (frontend.tf) only fronts the
# static frontend bucket, not this API's App Runner service, and no Terraform
# resource ever generates/sets this secret — it is permanently empty today, so
# this check is a no-op and the raw App Runner URL is directly reachable. Real
# origin-cloaking needs an App Runner-backed CloudFront distribution in front
# of the API first (tracked: s29 P1-B); until then, the demo's abuse ceiling
# is the per-IP rate limits + concurrency gate below, not this header.
# "/health" stays open for App Runner's own health checks.
# ---------------------------------------------------------------------------
_GATED_PREFIXES = ("/ask", "/sql", "/explore", "/profile", "/demo")
# A hand-rolled counter (not asyncio.Semaphore) for two reasons: (1) the
# check-then-acquire has to be atomic under an asyncio.Lock, whereas
# Semaphore.locked() followed by a separate acquire() is two steps with a gap
# a burst of concurrent requests can slip through — they'd see locked()==False
# and then queue on acquire() instead of getting the intended fast 503; and
# (2) the slot must stay held for the lifetime of a StreamingResponse body
# (the flagship /ask/stream surface), not just until call_next() returns the
# initial response, or SSE streams pile up past demo_max_concurrency invisibly.
_demo_lock = asyncio.Lock()
_demo_inflight = 0


async def _try_acquire_demo_slot() -> bool:
    global _demo_inflight
    async with _demo_lock:
        if _demo_inflight >= settings.demo_max_concurrency:
            return False
        _demo_inflight += 1
        return True


async def _release_demo_slot() -> None:
    global _demo_inflight
    async with _demo_lock:
        _demo_inflight -= 1


@app.middleware("http")
async def _demo_guards(request: Request, call_next: Callable[[Request], Awaitable[Any]]) -> Any:
    if settings.origin_verify_secret and request.url.path != "/health":
        if request.headers.get("x-origin-verify") != settings.origin_verify_secret:
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})
    if not settings.demo_mode or not request.url.path.startswith(_GATED_PREFIXES):
        return await call_next(request)
    if not await _try_acquire_demo_slot():
        return JSONResponse(
            status_code=503,
            content={"detail": "demo_full"},
            headers={"Retry-After": "15"},
        )
    released = False

    async def _release_once() -> None:
        nonlocal released
        if not released:
            released = True
            await _release_demo_slot()

    try:
        response = await call_next(request)
    except BaseException:
        await _release_once()
        raise
    if isinstance(response, StreamingResponse):
        original_iterator = response.body_iterator

        async def _tracked_body() -> Any:
            try:
                async for chunk in original_iterator:
                    yield chunk
            finally:
                await _release_once()

        response.body_iterator = _tracked_body()
        return response
    await _release_once()
    return response


@app.exception_handler(Exception)
async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    """Return unhandled errors as JSON 500s that still carry CORS headers.

    Starlette runs this handler in ServerErrorMiddleware — *outside*
    CORSMiddleware — so without the manual header below a browser on another
    origin (the CloudFront frontend) is forbidden from reading the response and
    reports only "TypeError: Failed to fetch". That turned a plain
    UndefinedTableError into an undiagnosable blank Explore tab in prod
    (2026-07-21); with this handler the client sees a real 500 + detail.
    The exception is re-logged with its traceback, same as the default handler.

    Waking-database failures (s29) are split out as a retryable 503: while
    Aurora resumes from auto-pause every connect fails, which is a state the
    client can wait out — the login flow retries on exactly this detail string
    (frontend/src/lib/auth.ts) instead of dumping the user back to the card.
    A real 500 must never wear that label, so the check is the narrow
    connect-phase classifier in db.is_db_waking, and everything else keeps the
    existing 500 path.
    """
    if is_db_waking(exc):
        log.warning("db waking on %s %s: %s", request.method, request.url.path, exc, exc_info=False)
        response = JSONResponse(
            status_code=503,
            content={"detail": "db_warming"},
            headers={"Retry-After": "5"},
        )
    else:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        response = JSONResponse(status_code=500, content={"detail": "Internal server error"})
    origin = request.headers.get("origin")
    if origin and origin in settings.all_cors_origins:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Vary"] = "Origin"
    return response


app.include_router(auth.router)
app.include_router(ask.router)
app.include_router(events.router)
app.include_router(sql.router)
app.include_router(feedback.router)
app.include_router(goldens.router)
app.include_router(admin_config.router)
app.include_router(profile.router)
app.include_router(explore.router)
app.include_router(evals.router)
app.include_router(ops.router)
app.include_router(integrations.router)
app.include_router(service_accounts.router)
app.include_router(analytics.router)
app.include_router(architecture.router)
app.include_router(admin_pack.router)

# s36: the MCP front door, mounted rather than run as its own service. The gate
# wrapper authenticates a dpk_ key pinned to surface='mcp' before the JSON-RPC
# transport sees anything, so /mcp has no anonymous path.
app.mount("/mcp", mcp_gate)

# Instrumented once every route is registered, so spans carry route templates
# (/conversations/{conversation_id}) rather than raw paths — otherwise
# per-endpoint latency can't be aggregated (s32 W2).
instrument_app(app)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.app_env}


@app.get("/metrics")
async def prometheus_metrics() -> Any:
    """s40 M1 (D4): the Prometheus scrape surface — queue depth/shed/wait plus
    whatever else registers on the default registry. Text format, no auth: the
    obs profile's prometheus is the only intended caller and the metrics carry
    no user data."""
    from fastapi import Response
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


_HEALTH_DB_MIN_INTERVAL_S = 5.0
_health_db_cache: dict[str, str] | None = None
_health_db_cache_at: float = 0.0


@app.get("/health/db")
async def health_db(request: Request) -> dict[str, str]:
    """Unauthenticated DB wake probe (s29).

    The login card fires this on mount so Aurora starts resuming while the
    user is still in the Google sign-in dance (~12s observed) instead of when
    the first credentialed /me arrives — front-loading most of the ~30s wake.
    A waking database is the expected cold-visit answer, not an error, so it
    reports "waking" at 200 rather than tripping the 503 path; anything the
    classifier doesn't recognise still raises into the normal error handler.

    Only requests carrying the app's channel marker (X-Client-Channel: web,
    sent by frontend wakeDb) touch the database: every probe is a fresh
    NullPool connect that resumes a paused Aurora, so a generic poller — an
    uptime monitor, a scanner — pointed here would defeat auto-pause, the
    dominant idle cost. The marker is a fence against that traffic, not a
    secret; unmarked requests get a 200 saying the probe was skipped.

    The marker alone doesn't stop a caller who copies it from the shipped
    bundle from hammering this endpoint to keep forcing fresh connects, so
    real probes are also coalesced: a marked request within
    _HEALTH_DB_MIN_INTERVAL_S of the last one gets the cached result instead
    of opening another connection, capping how often this path can wake
    Aurora regardless of request volume.
    """
    if request.headers.get("x-client-channel") != "web":
        return {"status": "skipped", "env": settings.app_env}
    global _health_db_cache, _health_db_cache_at
    now = time.monotonic()
    if _health_db_cache is not None and now - _health_db_cache_at < _HEALTH_DB_MIN_INTERVAL_S:
        return _health_db_cache
    try:
        async with engine.connect() as conn:
            await conn.execute(text("select 1"))
    except Exception as exc:
        if not is_db_waking(exc):
            raise
        result = {"status": "waking", "env": settings.app_env}
        _health_db_cache, _health_db_cache_at = result, now
        return result
    result = {"status": "ok", "env": settings.app_env}
    _health_db_cache, _health_db_cache_at = result, now
    return result
