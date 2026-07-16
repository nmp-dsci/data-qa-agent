from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

from ..auth import CurrentUser, get_optional_user, require_admin
from ..db import jsonable, rls_connection

router = APIRouter(tags=["events"])


class EventIn(BaseModel):
    event_type: str
    session_id: str | None = None
    payload: dict[str, Any] = {}


@router.post("/events", status_code=201)
async def track_event(
    body: EventIn, user: CurrentUser | None = Depends(get_optional_user)
) -> dict[str, str]:
    """Frontend product-analytics sink. Accepts pre-login events (no user)."""
    user_id = user.id if user else None
    async with rls_connection(user_id) as conn:
        await conn.execute(
            text(
                "INSERT INTO app.events (user_id, session_id, event_type, payload) "
                "VALUES (:uid, :sid, :etype, CAST(:payload AS jsonb))"
            ),
            {
                "uid": user_id,
                "sid": body.session_id,
                "etype": body.event_type,
                "payload": json.dumps(body.payload),
            },
        )
    return {"status": "ok"}


@router.get("/admin/events")
async def list_events(
    limit: int = 50, since: str | None = None, admin: CurrentUser = Depends(require_admin)
) -> list[dict[str, Any]]:
    """``since`` (ISO timestamp) filters to events at/after that time — used by the
    admin dashboard's 7d metric so the headline count + sparkline reflect the
    actual window instead of silently truncating at ``limit`` most-recent rows."""
    async with rls_connection(admin.id) as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT e.id, e.event_type, e.created_at, e.payload, u.username "
                        "FROM app.events e LEFT JOIN app.users u ON u.id = e.user_id "
                        "WHERE (:since::timestamptz IS NULL "
                        "OR e.created_at >= :since::timestamptz) "
                        "ORDER BY e.created_at DESC LIMIT :lim"
                    ),
                    {"lim": limit, "since": since},
                )
            )
            .mappings()
            .all()
        )
    return [{k: jsonable(v) for k, v in r.items()} for r in rows]


@router.get("/admin/users")
async def list_users(admin: CurrentUser = Depends(require_admin)) -> list[dict[str, Any]]:
    async with rls_connection(admin.id) as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT u.id, u.username, u.email, u.display_name, u.role, "
                        "MAX(e.created_at) AS last_active "
                        "FROM app.users u LEFT JOIN app.events e ON e.user_id = u.id "
                        "GROUP BY u.id ORDER BY u.username"
                    )
                )
            )
            .mappings()
            .all()
        )
    return [{k: jsonable(v) for k, v in r.items()} for r in rows]


@router.get("/admin/datasets")
async def list_datasets(admin: CurrentUser = Depends(require_admin)) -> list[dict[str, Any]]:
    async with rls_connection(admin.id) as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT d.id, d.slug, d.name, d.status, d.row_count, "
                        "(SELECT count(*) FROM app.dataset_access da WHERE da.dataset_id = d.id) "
                        "AS access_count "
                        "FROM app.datasets d ORDER BY d.slug"
                    )
                )
            )
            .mappings()
            .all()
        )
    return [{k: jsonable(v) for k, v in r.items()} for r in rows]


@router.get("/admin/query-runs")
async def list_query_runs(
    limit: int = 50, since: str | None = None, admin: CurrentUser = Depends(require_admin)
) -> list[dict[str, Any]]:
    """``since`` (ISO timestamp) filters to runs at/after that time — see
    list_events for why the admin dashboard's 7d metric needs this."""
    async with rls_connection(admin.id) as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT qr.id, qr.created_at, u.username, d.slug AS dataset, "
                        "qr.engine, qr.source, qr.channel, qr.row_count, qr.latency_ms, "
                        "qr.status, qr.question, qr.sql_text, qr.error, "
                        "qr.input_tokens, qr.output_tokens, qr.trace "
                        "FROM app.query_runs qr "
                        "JOIN app.users u ON u.id = qr.user_id "
                        "LEFT JOIN app.datasets d ON d.id = qr.dataset_id "
                        "WHERE (:since::timestamptz IS NULL "
                        "OR qr.created_at >= :since::timestamptz) "
                        "ORDER BY qr.created_at DESC LIMIT :lim"
                    ),
                    {"lim": limit, "since": since},
                )
            )
            .mappings()
            .all()
        )
    return [{k: jsonable(v) for k, v in r.items()} for r in rows]
