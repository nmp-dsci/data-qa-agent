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
from ..db import admin_ro_connection, jsonable

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
    async with admin_ro_connection() as conn:
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


# ---------------------------------------------------------------------------
# Handover analytics (s48 §7) — what happened to a deck after we handed it
# over. Fed by scripts/handover_poll.py, which fills artifact_opened_at /
# artifact_edit_count on query_runs and app.artifact_edits per change.
# ---------------------------------------------------------------------------

_HANDOVER_WINDOW = (
    "artifact_deck_url IS NOT NULL AND created_at >= now() - make_interval(days => :days)"
)


@router.get("/analytics/handover")
async def analytics_handover(
    days: int = 30, admin: CurrentUser = Depends(require_admin)
) -> dict[str, Any]:
    days = max(1, min(days, 90))
    async with admin_ro_connection() as conn:
        decks = await _one(
            conn, f"SELECT count(*) FROM app.query_runs WHERE {_HANDOVER_WINDOW}", days=days
        )
        opened = await _one(
            conn,
            f"SELECT count(*) FROM app.query_runs "
            f"WHERE {_HANDOVER_WINDOW} AND artifact_opened_at IS NOT NULL",
            days=days,
        )
        edited = await _one(
            conn,
            f"SELECT count(*) FROM app.query_runs "
            f"WHERE {_HANDOVER_WINDOW} AND artifact_edit_count > 0",
            days=days,
        )
        # Time from "the deck existed" to "the first recorded edit", in
        # minutes — the honest read of how long a handed-over deck sits before
        # anyone touches it. NULL (no edited decks in the window) rather than
        # 0, which would read as "everyone edits instantly".
        median_minutes = await _one(
            conn,
            "WITH first_edit AS ("
            "  SELECT run_id, min(observed_at) AS t FROM app.artifact_edits GROUP BY run_id"
            ") "
            "SELECT percentile_cont(0.5) WITHIN GROUP ("
            "  ORDER BY extract(epoch FROM (fe.t - qr.created_at)) / 60"
            ") "
            f"FROM app.query_runs qr JOIN first_edit fe ON fe.run_id = qr.id "
            f"WHERE {_HANDOVER_WINDOW.replace('created_at', 'qr.created_at')}",
            days=days,
        )
        edits_by_event = await _rows(
            conn,
            "SELECT ae.event, count(*) AS edits "
            "FROM app.artifact_edits ae JOIN app.query_runs qr ON qr.id = ae.run_id "
            f"WHERE {_HANDOVER_WINDOW.replace('created_at', 'qr.created_at')} "
            "GROUP BY ae.event ORDER BY edits DESC",
            days=days,
        )
        edits_by_layout = await _rows(
            conn,
            "SELECT ae.layout_id, count(DISTINCT ae.run_id) AS decks, count(*) AS edits, "
            "  count(*) FILTER (WHERE ae.event = 'headline_changed') AS headline_edits, "
            "  count(*) FILTER (WHERE ae.event IN "
            "    ('chart_added', 'chart_deleted', 'chart_type_changed')) AS chart_edits "
            "FROM app.artifact_edits ae JOIN app.query_runs qr ON qr.id = ae.run_id "
            f"WHERE {_HANDOVER_WINDOW.replace('created_at', 'qr.created_at')} "
            "  AND ae.layout_id IS NOT NULL "
            "GROUP BY ae.layout_id ORDER BY edits DESC",
            days=days,
        )
        layout_usage = await _rows(
            conn,
            "SELECT layout_id, count(*) AS slides FROM ("
            "  SELECT coalesce(s->>'layout_id', s->>'layout') AS layout_id "
            "  FROM app.query_runs qr "
            "  JOIN app.messages m ON m.id = qr.message_id "
            "  CROSS JOIN LATERAL jsonb_array_elements("
            "    coalesce(m.report -> 'artifact' -> 'slides', '[]'::jsonb)"
            "  ) AS s "
            f"  WHERE {_HANDOVER_WINDOW.replace('created_at', 'qr.created_at')}"
            ") t WHERE layout_id IS NOT NULL GROUP BY layout_id ORDER BY slides DESC",
            days=days,
        )
        recent = await _rows(
            conn,
            "SELECT qr.id AS run_id, qr.question, qr.artifact_deck_url AS deck_url, "
            "  qr.artifact_edit_count AS edits, "
            "  (SELECT max(observed_at) FROM app.artifact_edits "
            "    WHERE run_id = qr.id) AS last_edit_at "
            f"FROM app.query_runs qr WHERE {_HANDOVER_WINDOW} "
            "ORDER BY qr.created_at DESC LIMIT 10",
            days=days,
        )

    decks_n = int(decks or 0)
    return {
        "days": days,
        "decks": decks_n,
        "opened": int(opened or 0),
        "edited": int(edited or 0),
        "edit_rate": (int(edited or 0) / decks_n) if decks_n else None,
        "median_minutes_to_first_edit": (
            float(median_minutes) if median_minutes is not None else None
        ),
        "edits_by_event": edits_by_event,
        "edits_by_layout": edits_by_layout,
        "layout_usage": layout_usage,
        "recent": recent,
    }
