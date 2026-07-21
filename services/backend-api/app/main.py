from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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
    """
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
