from __future__ import annotations

import json
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from ..agent_client import assist_sql_on_agent, fetch_catalog, run_sql_on_agent
from ..auth import CurrentUser, get_current_user
from ..channel import get_channel
from ..db import jsonable, rls_connection
from ..limits import check_daily_llm_cap

router = APIRouter(tags=["sql"])


class SqlRequest(BaseModel):
    sql: str


class SqlResponse(BaseModel):
    columns: list[str] = []
    rows: list[list[Any]] = []
    row_count: int = 0
    truncated: bool = False
    latency_ms: int | None = None
    engine: str = "sql_editor"
    error: str | None = None


async def _log_event(
    conn: Any, user_id: str, event_type: str, payload: dict[str, Any] | None = None
) -> None:
    await conn.execute(
        text(
            "INSERT INTO app.events (user_id, event_type, payload) "
            "VALUES (:uid, :etype, CAST(:payload AS jsonb))"
        ),
        {"uid": user_id, "etype": event_type, "payload": json.dumps(payload or {})},
    )


@router.post("/sql", response_model=SqlResponse)
async def run_sql(
    body: SqlRequest,
    user: CurrentUser = Depends(get_current_user),
    channel: str = Depends(get_channel),
) -> SqlResponse:
    """Run user-authored SQL through the data-agent's governed read-only executor.

    The editor never touches the DB via this service's read/write ``app_user``
    role — it delegates to the data-agent (``agent_ro``), so the same read-only,
    RLS-scoped, SELECT-only, timeout- and row-capped guardrails as the agent
    apply. Every run is audited in ``app.query_runs`` (``source = 'sql_editor'``).
    """
    sql = body.sql.strip()
    if not sql:
        raise HTTPException(status_code=400, detail="SQL must not be empty")

    async with rls_connection(user.id) as conn:
        await _log_event(conn, user.id, "sql_query_submitted", {"length": len(sql)})

    started = time.perf_counter()
    try:
        result = await run_sql_on_agent(sql=sql, user_id=user.id, role=user.role)
    except httpx.HTTPError as exc:
        async with rls_connection(user.id) as conn:
            await _log_event(conn, user.id, "sql_query_failed", {"error": str(exc)})
        raise HTTPException(status_code=502, detail=f"Agent unavailable: {exc}") from exc
    latency_ms = int((time.perf_counter() - started) * 1000)

    error = result.get("error")
    columns = result.get("columns", [])
    rows = result.get("rows", [])
    row_count = int(result.get("row_count", 0))
    status = "error" if error else "success"

    # Audit the run (no conversation/NL-question for editor runs — those columns
    # are nullable; source distinguishes it from agent runs in the dashboard).
    async with rls_connection(user.id) as conn:
        await conn.execute(
            text(
                "INSERT INTO app.query_runs "
                "(user_id, sql_text, engine, row_count, latency_ms, status, error, source, "
                "channel) "
                "VALUES (:uid, :sql, 'sql_editor', :rc, :lat, :status, :err, 'sql_editor', "
                ":channel)"
            ),
            {
                "uid": user.id,
                "sql": result.get("sql", sql),
                "rc": row_count,
                "lat": latency_ms,
                "status": status,
                "err": error,
                "channel": channel,
            },
        )
        await _log_event(
            conn,
            user.id,
            "sql_query_failed" if error else "sql_query_succeeded",
            {"latency_ms": latency_ms, "row_count": row_count},
        )

    return SqlResponse(
        columns=columns,
        rows=rows,
        row_count=row_count,
        truncated=bool(result.get("truncated", False)),
        latency_ms=latency_ms,
        error=error,
    )


@router.get("/sql/history")
async def sql_history(
    limit: int = 20, user: CurrentUser = Depends(get_current_user)
) -> list[dict[str, Any]]:
    """The current user's recent SQL-editor runs (Phase B — click to reload)."""
    limit = max(1, min(limit, 100))
    async with rls_connection(user.id) as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT id, created_at, sql_text, row_count, latency_ms, status, error "
                        "FROM app.query_runs "
                        "WHERE user_id = :uid AND source = 'sql_editor' "
                        "ORDER BY created_at DESC LIMIT :lim"
                    ),
                    {"uid": user.id, "lim": limit},
                )
            )
            .mappings()
            .all()
        )
    return [{k: jsonable(v) for k, v in r.items()} for r in rows]


class SqlAiRequest(BaseModel):
    action: str = "generate"  # generate | explain | fix | optimize
    prompt: str | None = None  # natural-language ask (for 'generate')
    sql: str | None = None  # the SQL to act on (for explain/fix/optimize)


class SqlAiResponse(BaseModel):
    sql: str | None = None
    explanation: str | None = None
    engine: str = "stub"
    error: str | None = None


@router.post("/sql/ai", response_model=SqlAiResponse)
async def sql_ai(
    body: SqlAiRequest, user: CurrentUser = Depends(get_current_user)
) -> SqlAiResponse:
    """AI assist for the editor (Phase C) — reuses the data-agent's SQL brain.

    generate: natural language -> SQL (dropped into the editor and auto-run
    client-side). explain/fix/optimize: transform the selected SQL. Same governed
    executor still runs whatever SQL comes back, so this never bypasses RLS.
    Counts against the same per-user daily LLM cap as /ask (s12) — the
    sql_ai_requested event logged below is what the cap tallies.
    """
    action = body.action.strip().lower()
    if action not in {"generate", "explain", "fix", "optimize"}:
        raise HTTPException(status_code=400, detail=f"Unknown action: {body.action}")
    if action == "generate" and not (body.prompt and body.prompt.strip()):
        raise HTTPException(status_code=400, detail="A prompt is required to generate SQL")
    if action != "generate" and not (body.sql and body.sql.strip()):
        raise HTTPException(status_code=400, detail="SQL is required for this action")
    await check_daily_llm_cap(user)

    async with rls_connection(user.id) as conn:
        await _log_event(conn, user.id, "sql_ai_requested", {"action": action})

    try:
        result = await assist_sql_on_agent(
            action=action,
            prompt=body.prompt,
            sql=body.sql,
            user_id=user.id,
            role=user.role,
        )
    except httpx.HTTPError as exc:
        async with rls_connection(user.id) as conn:
            await _log_event(conn, user.id, "sql_ai_failed", {"error": str(exc)})
        raise HTTPException(status_code=502, detail=f"Agent unavailable: {exc}") from exc

    error = result.get("error")
    async with rls_connection(user.id) as conn:
        await _log_event(
            conn,
            user.id,
            "sql_ai_failed" if error else "sql_ai_succeeded",
            {"action": action, "engine": result.get("engine")},
        )
    return SqlAiResponse(
        sql=result.get("sql"),
        explanation=result.get("explanation"),
        engine=result.get("engine", "stub"),
        error=error,
    )


@router.get("/schema/catalog")
async def schema_catalog(user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """Structured schema for the SQL editor's browser (proxied from the agent)."""
    try:
        return await fetch_catalog(role=user.role)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Agent unavailable: {exc}") from exc
