"""Self-service profile endpoints for the Settings tab.

Memories: app.user_memories is strictly owner-only under RLS (no admin
override), so listing/deleting under the caller's RLS context can only ever
touch their own rows. Access: the caller's role + dataset grants, read from the
same tables the admin panel uses.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text

from ..auth import CurrentUser, get_current_user
from ..db import jsonable, rls_connection

router = APIRouter(tags=["profile"])


@router.get("/me/memories")
async def list_my_memories(
    user: CurrentUser = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """The agent's remembered preferences for the current user."""
    async with rls_connection(user.id) as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT id, kind, content, created_at, last_used_at "
                        "FROM app.user_memories ORDER BY created_at DESC LIMIT 100"
                    )
                )
            )
            .mappings()
            .all()
        )
    return [{k: jsonable(v) for k, v in r.items()} for r in rows]


@router.delete("/me/memories/{memory_id}")
async def delete_my_memory(
    memory_id: str,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, bool]:
    """Forget one remembered preference (owner-only via RLS)."""
    async with rls_connection(user.id) as conn:
        result = await conn.execute(
            text("DELETE FROM app.user_memories WHERE id = :mid"),
            {"mid": memory_id},
        )
    return {"deleted": (result.rowcount or 0) > 0}


@router.get("/me/access")
async def my_access(user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """The caller's role and dataset grants, for the Settings access summary."""
    async with rls_connection(user.id) as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT d.slug, d.name, d.status, da.access "
                        "FROM app.dataset_access da "
                        "JOIN app.datasets d ON d.id = da.dataset_id "
                        "WHERE da.user_id = current_setting('app.current_user_id', true)::uuid "
                        "ORDER BY d.slug"
                    )
                )
            )
            .mappings()
            .all()
        )
    return {
        "role": user.role,
        "rls_note": (
            "Admins read across users; RLS isolates everyone else to their own rows."
            if user.role == "admin"
            else "Row-Level Security scopes every query to your own grants."
        ),
        "datasets": [{k: jsonable(v) for k, v in r.items()} for r in rows],
    }
