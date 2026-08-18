"""Visitor analytics rollups (s38 P2.5) — the Analytics tab's data source.

First-party analytics over the event log the app already had: the frontend
beacon (``lib/analytics.ts``) posts named events with an anonymous
``visitor_id`` (localStorage) + ``session_id`` (sessionStorage) into
``app.events``, and this router aggregates them for the admin-only Analytics
tab. Not Google Analytics, deliberately: the demo's audience runs ad-blockers
that eat third-party trackers, and a same-origin API call sails through.

Admin-only — visitors are the subject of this data, never its audience. Runs
on the elevated read-only role (``admin_ro``) because the aggregates span every
user's events, which no single RLS context can see; the same pattern and the
same engine the ops rollup uses.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from ..auth import CurrentUser, require_admin
from ..db import admin_ro_engine, jsonable

router = APIRouter(tags=["analytics"])

# The funnel, in order: how far does a visitor get? Counts are distinct
# visitors reaching each stage in the window.
_FUNNEL_STAGES = [
    ("demo_landing_view", "Landed"),
    ("demo_enter_click", "Entered the demo"),
    ("agent_started", "Asked a question"),
    ("sql_query_submitted", "Ran SQL"),
    ("explore_view", "Opened Explore"),
    ("outbound_click", "Clicked GitHub/LinkedIn"),
]

# visitor_id lives in the event payload (the beacon adds it to every event);
# session_id is a real column. Older / server-side events carry neither and
# fall back to session or user grouping.
_VISITOR = "coalesce(payload->>'visitor_id', session_id, user_id::text)"


async def _one(conn: AsyncConnection, sql: str, **params: Any) -> Any:
    return (await conn.execute(text(sql), params)).scalar()


async def _rows(conn: AsyncConnection, sql: str, **params: Any) -> list[dict[str, Any]]:
    result = (await conn.execute(text(sql), params)).mappings().all()
    return [{k: jsonable(v) for k, v in r.items()} for r in result]


@router.get("/analytics/summary")
async def analytics_summary(
    days: int = 14, admin: CurrentUser = Depends(require_admin)
) -> dict[str, Any]:
    days = max(1, min(days, 90))
    async with admin_ro_engine.connect() as conn:
        window = "created_at >= now() - make_interval(days => :days)"

        total_visitors = await _one(
            conn,
            f"SELECT count(DISTINCT {_VISITOR}) FROM app.events WHERE {window}",
            days=days,
        )
        today_visitors = await _one(
            conn,
            f"SELECT count(DISTINCT {_VISITOR}) FROM app.events "
            "WHERE created_at >= date_trunc('day', now())",
        )
        sessions = await _one(
            conn,
            f"SELECT count(DISTINCT session_id) FROM app.events "
            f"WHERE {window} AND session_id IS NOT NULL",
            days=days,
        )
        total_events = await _one(
            conn, f"SELECT count(*) FROM app.events WHERE {window}", days=days
        )
        # Returning = a visitor id seen on more than one distinct day. This is
        # the honest browser-level definition (s38: no fingerprinting).
        returning = await _one(
            conn,
            f"SELECT count(*) FROM ("
            f"  SELECT {_VISITOR} AS v FROM app.events WHERE {window} "
            f"  GROUP BY 1 HAVING count(DISTINCT date_trunc('day', created_at)) > 1"
            f") t",
            days=days,
        )

        funnel = []
        for event_type, label in _FUNNEL_STAGES:
            count = await _one(
                conn,
                f"SELECT count(DISTINCT {_VISITOR}) FROM app.events "
                f"WHERE {window} AND event_type = :etype",
                days=days,
                etype=event_type,
            )
            funnel.append({"event": event_type, "label": label, "visitors": int(count or 0)})

        daily = await _rows(
            conn,
            f"SELECT date_trunc('day', created_at)::date AS day, "
            f"  count(*) AS events, count(DISTINCT {_VISITOR}) AS visitors "
            f"FROM app.events WHERE {window} GROUP BY 1 ORDER BY 1",
            days=days,
        )
        top_events = await _rows(
            conn,
            f"SELECT event_type, count(*) AS count FROM app.events "
            f"WHERE {window} GROUP BY 1 ORDER BY 2 DESC LIMIT 12",
            days=days,
        )
        # What visitors actually asked — including free-text misses, which are
        # the recording backlog: record those runs in dev, they become chips.
        top_questions = await _rows(
            conn,
            "SELECT question, count(*) AS count, max(engine) AS engine "
            "FROM app.query_runs "
            "WHERE question IS NOT NULL "
            "  AND created_at >= now() - make_interval(days => :days) "
            "GROUP BY question ORDER BY count DESC, max(created_at) DESC LIMIT 10",
            days=days,
        )
        recent_sessions = await _rows(
            conn,
            f"SELECT session_id, min(created_at) AS started, max(created_at) AS last_seen, "
            f"  count(*) AS events, "
            f"  string_agg(DISTINCT event_type, ', ' ORDER BY event_type) AS event_types "
            f"FROM app.events WHERE {window} AND session_id IS NOT NULL "
            f"GROUP BY session_id ORDER BY max(created_at) DESC LIMIT 10",
            days=days,
        )

    return {
        "days": days,
        "totals": {
            "visitors": int(total_visitors or 0),
            "today_visitors": int(today_visitors or 0),
            "sessions": int(sessions or 0),
            "events": int(total_events or 0),
            "returning_visitors": int(returning or 0),
        },
        "funnel": funnel,
        "daily": daily,
        "top_events": top_events,
        "top_questions": top_questions,
        "recent_sessions": recent_sessions,
    }
