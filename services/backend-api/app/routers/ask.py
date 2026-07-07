from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from ..agent_client import ask_agent
from ..auth import CurrentUser, get_current_user
from ..channel import get_channel
from ..db import rls_connection

router = APIRouter(tags=["ask"])

# Conversations/audit are attributed to the sales dataset; the agent's SQL may
# span both marts and RLS still scopes rows per the user's grants on each.
DATASET_SLUG = "nsw_sales"


class AskRequest(BaseModel):
    question: str
    conversation_id: str | None = None


class AskResponse(BaseModel):
    conversation_id: str
    message_id: str
    # The app.query_runs row id for this question — the stable handle for
    # diagnosing a specific run (scripts/inspect_run.py, admin Query Runs).
    run_id: str
    answer: str
    sql: str | None = None
    columns: list[str] = []
    rows: list[list[Any]] = []
    row_count: int = 0
    chart: dict[str, Any] | None = None
    engine: str = "stub"
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    # Step-by-step agent trace — only populated for admins (gated below).
    steps: list[dict[str, Any]] = []
    # Structured InsightReport (K2) — present on the LLM path; None for the stub.
    report: dict[str, Any] | None = None
    # Pages contract (s07): Summary → Insights pages of governed objects the
    # frontend's template registry renders with visx. Also embedded in the
    # stored report (messages.report.pages) so history reopen restores them.
    pages: list[dict[str, Any]] | None = None


async def _log_event(conn: Any, user_id: str, event_type: str, payload: dict | None = None) -> None:
    await conn.execute(
        text(
            "INSERT INTO app.events (user_id, event_type, payload) "
            "VALUES (:uid, :etype, CAST(:payload AS jsonb))"
        ),
        {"uid": user_id, "etype": event_type, "payload": _json(payload or {})},
    )


def _json(obj: Any) -> str:
    import json

    return json.dumps(obj)


@router.post("/ask", response_model=AskResponse)
async def ask(
    body: AskRequest,
    user: CurrentUser = Depends(get_current_user),
    channel: str = Depends(get_channel),
) -> AskResponse:
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question must not be empty")

    # tx1: ensure conversation, record the user's message, mark agent as started.
    async with rls_connection(user.id) as conn:
        conversation_id = body.conversation_id
        if conversation_id is None:
            conversation_id = str(
                (
                    await conn.execute(
                        text(
                            "INSERT INTO app.conversations (user_id, dataset_id, title) "
                            "VALUES (:uid, (SELECT id FROM app.datasets WHERE slug = :slug), "
                            ":title) RETURNING id"
                        ),
                        {"uid": user.id, "slug": DATASET_SLUG, "title": question[:60]},
                    )
                ).scalar_one()
            )
        await conn.execute(
            text(
                "INSERT INTO app.messages (conversation_id, user_id, role, content) "
                "VALUES (:cid, :uid, 'user', :content)"
            ),
            {"cid": conversation_id, "uid": user.id, "content": question},
        )
        await _log_event(conn, user.id, "agent_started", {"question": question})

    # Delegate to the agent (its own connection enforces the same RLS).
    started = time.perf_counter()
    try:
        result = await ask_agent(
            question=question, user_id=user.id, role=user.role, dataset_slug=DATASET_SLUG
        )
    except httpx.HTTPError as exc:  # noqa: BLE001
        async with rls_connection(user.id) as conn:
            await _log_event(conn, user.id, "agent_error", {"error": str(exc)})
        raise HTTPException(status_code=502, detail=f"Agent unavailable: {exc}") from exc
    latency_ms = int((time.perf_counter() - started) * 1000)

    answer = result.get("answer", "")
    sql = result.get("sql")
    engine = result.get("engine", "stub")
    row_count = int(result.get("row_count", 0))
    input_tokens = result.get("input_tokens")
    output_tokens = result.get("output_tokens")
    steps = result.get("steps") or []
    report = result.get("report")
    pages = result.get("pages")

    # tx2: record the assistant's answer and mark agent as finished.
    async with rls_connection(user.id) as conn:
        message_id = str(
            (
                await conn.execute(
                    text(
                        "INSERT INTO app.messages "
                        "(conversation_id, user_id, role, content, sql_generated, latency_ms, "
                        "report) "
                        "VALUES (:cid, :uid, 'assistant', :content, :sql, :lat, "
                        "CAST(:report AS jsonb)) RETURNING id"
                    ),
                    {
                        "cid": conversation_id,
                        "uid": user.id,
                        "content": answer,
                        "sql": sql,
                        "lat": latency_ms,
                        "report": _json(report) if report is not None else None,
                    },
                )
            ).scalar_one()
        )
        run_id = str(
            (
                await conn.execute(
                    text(
                        "INSERT INTO app.query_runs "
                        "(conversation_id, message_id, user_id, dataset_id, question, "
                        "sql_text, engine, row_count, latency_ms, status, input_tokens, "
                        "output_tokens, trace, channel) "
                        "VALUES (:cid, :mid, :uid, "
                        "(SELECT id FROM app.datasets WHERE slug = :slug), :question, :sql, "
                        ":engine, :row_count, :lat, 'success', :in_tok, :out_tok, "
                        "CAST(:trace AS jsonb), :channel) RETURNING id"
                    ),
                    {
                        "cid": conversation_id,
                        "mid": message_id,
                        "uid": user.id,
                        "slug": DATASET_SLUG,
                        "question": question,
                        "sql": sql,
                        "engine": engine,
                        "row_count": row_count,
                        "lat": latency_ms,
                        "in_tok": input_tokens,
                        "out_tok": output_tokens,
                        "trace": _json(steps),
                        "channel": channel,
                    },
                )
            ).scalar_one()
        )
        await _log_event(
            conn, user.id, "agent_answered", {"latency_ms": latency_ms, "engine": engine}
        )

    return AskResponse(
        conversation_id=conversation_id,
        message_id=message_id,
        run_id=run_id,
        answer=answer,
        sql=sql,
        columns=result.get("columns", []),
        rows=result.get("rows", []),
        row_count=row_count,
        chart=result.get("chart"),
        engine=engine,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        # Only admins get the step-by-step trace in chat; it's still persisted for all runs.
        steps=steps if user.role == "admin" else [],
        report=report,
        pages=pages,
    )
