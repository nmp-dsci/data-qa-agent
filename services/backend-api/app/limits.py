from __future__ import annotations

import json
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from sqlalchemy import text

from .auth import CurrentUser
from .config import settings
from .db import rls_connection

# ---------------------------------------------------------------------------
# Demo-mode per-IP rate limiting (s38 P3).
#
# In-memory sliding windows, one deque of timestamps per (ip, scope). This is
# deliberately not a distributed limiter: demo deployments pin App Runner to a
# single instance (the cost ceiling), so process memory IS global state. An
# instance recycle resets the counters — acceptable; the attacker gains one
# fresh window against a capped instance. No-op outside demo mode.
# ---------------------------------------------------------------------------
_WINDOW_S = 60.0
_ip_windows: dict[tuple[str, str], deque[float]] = defaultdict(deque)


def client_ip(request: Request) -> str:
    """Best-effort caller IP: the LAST X-Forwarded-For hop, not the first.

    App Runner terminates the connection and appends the socket peer's real
    address to the end of the chain; anything earlier in the header is
    whatever the client itself chose to send and is not trustworthy — a
    caller can set X-Forwarded-For to a fabricated, rotating value to defeat
    a first-hop-based rate limit. Falls back to the socket peer if the header
    is absent entirely."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def check_demo_ip_rate(request: Request | None, scope: str, per_minute: int) -> None:
    """429 when this IP exceeded ``per_minute`` calls on ``scope`` in the last
    minute. The detail string is stable — the frontend maps it to a friendly
    sentence. 0 disables the scope's limit; outside demo mode, always a no-op.
    ``request`` is None for internal callers with no HTTP request (the MCP
    surface) — those are service-key-gated, so the limit does not apply."""
    if request is None or not settings.demo_mode or per_minute <= 0:
        return
    now = time.monotonic()
    window = _ip_windows[(client_ip(request), scope)]
    while window and now - window[0] > _WINDOW_S:
        window.popleft()
    if len(window) >= per_minute:
        raise HTTPException(
            status_code=429,
            detail="demo_rate_limited",
            headers={"Retry-After": "30"},
        )
    window.append(now)
    # Opportunistic pruning so a scanning attacker can't grow the dict forever:
    # drop fully-idle windows once the map gets large.
    if len(_ip_windows) > 10_000:
        for key in [k for k, w in _ip_windows.items() if not w or now - w[-1] > _WINDOW_S]:
            _ip_windows.pop(key, None)


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
        # A service account (s35) is shared by everyone on its surface — a whole
        # Slack channel behind one identity — so the per-person tiers are the
        # wrong shape for it and the free cap would be gone by lunchtime. It gets
        # its own limit rather than a role-based exemption: a machine identity
        # must never need to be an admin to function.
        if plan == "service":
            tier, limit = "service", settings.ask_daily_limit_service
        else:
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
            # s32 W0: a cap hit is a capacity signal, not a bug — recorded so the
            # deck's saturation panel can show "2 users hit the daily cap" beside
            # the concurrency ceiling, which is what tells you the limit is set
            # wrong rather than the service being unhealthy.
            await conn.execute(
                text(
                    "INSERT INTO app.events (user_id, event_type, payload) "
                    "VALUES (:uid, 'llm_cap_reached', CAST(:payload AS jsonb))"
                ),
                {"uid": user.id, "payload": json.dumps({"tier": tier, "limit": limit})},
            )
    if used >= limit:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Daily AI usage limit reached for the {tier} tier ({limit}/day). "
                "It resets at midnight UTC."
            ),
        )


async def check_daily_query_cap(user: CurrentUser) -> None:
    """Bound governed SELECTs per day — service accounts only (s36).

    ``check_daily_llm_cap`` guards every path that spends tokens. A raw governed
    query spends none, which is why a human's SQL-editor use is deliberately
    uncapped and stays that way.

    A service key is a different risk. It is a credential handed to a machine
    that can loop, and the MCP surface exposes ``run_governed_query`` directly to
    a model that will retry a failing query as readily as a person will not.
    Every statement still passes the full guard — SELECT-only, single statement,
    allowlisted tables, RLS-scoped, role ``statement_timeout`` — so this is a
    rate bound on a caller that cannot get tired, not a security control.

    Counts the account's own ``sql_editor`` runs today; RLS scopes the read to
    the account itself. A limit of 0 disables the cap.
    """
    if user.role == "admin":
        return
    limit = settings.sql_daily_limit_service
    if limit <= 0:
        return
    async with rls_connection(user.id) as conn:
        plan = (
            await conn.execute(text("SELECT plan FROM app.users WHERE id = :uid"), {"uid": user.id})
        ).scalar() or "free"
        if plan != "service":
            return
        used = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM app.query_runs "
                    "WHERE user_id = CAST(:uid AS uuid) AND source = 'sql_editor' "
                    "AND created_at >= date_trunc('day', now() AT TIME ZONE 'utc')"
                ),
                {"uid": user.id},
            )
        ).scalar_one()
        if used >= limit:
            # Same signal shape as the LLM cap so the ops deck's saturation panel
            # counts both without a second event type to teach it.
            await conn.execute(
                text(
                    "INSERT INTO app.events (user_id, event_type, payload) "
                    "VALUES (:uid, 'llm_cap_reached', CAST(:payload AS jsonb))"
                ),
                {"uid": user.id, "payload": json.dumps({"tier": "service_sql", "limit": limit})},
            )
    if used >= limit:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Daily governed-query limit reached for this service account "
                f"({limit}/day). It resets at midnight UTC."
            ),
        )
