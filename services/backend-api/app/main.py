from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .config import settings
from .db import engine, rls_connection
from .explore.manifest import ManifestError, validate_manifest
from .routers import (
    admin_config,
    ask,
    auth,
    evals,
    events,
    explore,
    feedback,
    goldens,
    profile,
    sql,
)
from .waking import is_db_waking

log = logging.getLogger("uvicorn.error")


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
    yield
    await engine.dispose()


app = FastAPI(title="data-qa-agent API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.all_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.app_env}


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
