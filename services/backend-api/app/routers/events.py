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
    limit: int = 50, admin: CurrentUser = Depends(require_admin)
) -> list[dict[str, Any]]:
    async with rls_connection(admin.id) as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT e.id, e.event_type, e.created_at, e.payload, u.username "
                        "FROM app.events e LEFT JOIN app.users u ON u.id = e.user_id "
                        "ORDER BY e.created_at DESC LIMIT :lim"
                    ),
                    {"lim": limit},
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
                        "SELECT id, username, email, display_name, role "
                        "FROM app.users ORDER BY username"
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
                    text("SELECT id, slug, name, status, row_count FROM app.datasets ORDER BY slug")
                )
            )
            .mappings()
            .all()
        )
    return [{k: jsonable(v) for k, v in r.items()} for r in rows]


@router.get("/admin/query-runs")
async def list_query_runs(
    limit: int = 50, admin: CurrentUser = Depends(require_admin)
) -> list[dict[str, Any]]:
    async with rls_connection(admin.id) as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT qr.id, qr.created_at, u.username, d.slug AS dataset, "
                        "qr.engine, qr.row_count, qr.latency_ms, qr.status, "
                        "qr.question, qr.sql_text, qr.error "
                        "FROM app.query_runs qr "
                        "JOIN app.users u ON u.id = qr.user_id "
                        "LEFT JOIN app.datasets d ON d.id = qr.dataset_id "
                        "ORDER BY qr.created_at DESC LIMIT :lim"
                    ),
                    {"lim": limit},
                )
            )
            .mappings()
            .all()
        )
    return [{k: jsonable(v) for k, v in r.items()} for r in rows]
