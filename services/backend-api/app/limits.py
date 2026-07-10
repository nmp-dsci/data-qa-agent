from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import text

from .auth import CurrentUser
from .config import settings
from .db import rls_connection


async def check_daily_llm_cap(user: CurrentUser) -> None:
    """Tiered per-user LLM cost cap (s12): reject beyond the tier's uses/day.

    One shared daily budget guards every LLM-backed path — /ask, /ask/stream
    and the SQL editor's /sql/ai assist — so the editor can't bypass the chat
    cap. Admins are uncapped (the owner runs the bill anyway). Paid tier =
    plan plus/pro; free tier = everyone else. Usage = the user's own persisted
    agent runs plus their sql_ai_requested events (RLS scopes both to them).
    The LLM is the dominant cost, so capping calls caps spend. A tier's limit
    of 0 disables its cap. Resets at midnight UTC.
    """
    if user.role == "admin":
        return
    async with rls_connection(user.id) as conn:
        plan = (
            await conn.execute(text("SELECT plan FROM app.users WHERE id = :uid"), {"uid": user.id})
        ).scalar() or "free"
        paid = plan in ("plus", "pro")
        tier, limit = (
            ("paid", settings.ask_daily_limit_paid)
            if paid
            else (
                "free",
                settings.ask_daily_limit_free,
            )
        )
        if limit <= 0:
            return
        result = await conn.execute(
            text(
                "SELECT ("
                "SELECT count(*) FROM app.query_runs "
                "WHERE user_id = CAST(:uid AS uuid) AND source = 'agent' "
                "AND created_at >= date_trunc('day', now() AT TIME ZONE 'utc')"
                ") + ("
                "SELECT count(*) FROM app.events "
                "WHERE user_id = CAST(:uid AS uuid) AND event_type = 'sql_ai_requested' "
                "AND created_at >= date_trunc('day', now() AT TIME ZONE 'utc')"
                ")"
            ),
            {"uid": user.id},
        )
        used = result.scalar_one()
    if used >= limit:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Daily AI usage limit reached for the {tier} tier ({limit}/day). "
                "It resets at midnight UTC."
            ),
        )
