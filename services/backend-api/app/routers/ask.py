from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from starlette.background import BackgroundTask

from ..agent_client import ask_agent, ask_agent_stream, title_agent
from ..agent_version import current_agent_version_id
from ..auth import CurrentUser, get_current_user
from ..channel import get_channel
from ..db import jsonable, rls_connection
from ..limits import check_daily_llm_cap
from ..scrub import scrub_text
from ..tracing import current_trace_id

router = APIRouter(tags=["ask"])

# Conversations/audit are attributed to the sales dataset; the agent's SQL may
# span both marts and RLS still scopes rows per the user's grants on each.
DATASET_SLUG = "nsw_sales"

# s32 W3: bound the one free-text field a user controls. Long enough for any
# real analytical question (the longest golden is ~200 chars), short enough that
# the question can't be used to stuff a prompt or a jsonb column. Enforced here
# rather than in the model so the rejection is a clean 400 with a reason.
MAX_QUESTION_CHARS = 2_000


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
    # s32 W1: this answer is not the one that was asked for — a retried-then-
    # stubbed run or an unreachable agent. The chat surface reads it to offer a
    # Retry affordance instead of leaving the user to guess whether to re-ask.
    degraded: bool = False
    # Step-by-step agent trace — only populated for admins (gated below).
    steps: list[dict[str, Any]] = []
    # Structured InsightReport (K2) — present on the LLM path; None for the stub.
    report: dict[str, Any] | None = None
    # Pages contract (s07): Summary → Insights pages of governed objects the
    # frontend's template registry renders with visx. Also embedded in the
    # stored report (messages.report.pages) so history reopen restores them.
    pages: list[dict[str, Any]] | None = None


async def _log_event(
    conn: Any, user_id: str, event_type: str, payload: dict[str, Any] | None = None
) -> None:
    await conn.execute(
        text(
            "INSERT INTO app.events (user_id, event_type, payload) "
            "VALUES (:uid, :etype, CAST(:payload AS jsonb))"
        ),
        {"uid": user_id, "etype": event_type, "payload": _json(payload or {})},
    )


def _json(obj: Any) -> str:
    return json.dumps(obj)


@router.get("/conversations")
async def list_conversations(
    user: CurrentUser = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """The current user's conversations for the Chat history sidebar.

    RLS already scopes rows; the explicit user_id filter keeps an admin's
    sidebar to their own threads even where admin read policies are broader.
    """
    async with rls_connection(user.id) as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT c.id, c.title, c.created_at, "
                        "  coalesce(max(m.created_at), c.created_at) AS last_at, "
                        "  count(m.id) AS message_count "
                        "FROM app.conversations c "
                        "LEFT JOIN app.messages m ON m.conversation_id = c.id "
                        "WHERE c.user_id = current_setting('app.current_user_id', true)::uuid "
                        "GROUP BY c.id, c.title, c.created_at "
                        "ORDER BY coalesce(max(m.created_at), c.created_at) DESC "
                        "LIMIT 100"
                    )
                )
            )
            .mappings()
            .all()
        )
    return [{k: jsonable(v) for k, v in r.items()} for r in rows]


@router.get("/conversations/{conversation_id}/messages")
async def conversation_messages(
    conversation_id: str,
    user: CurrentUser = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """A conversation's messages for reopen.

    Each assistant message is joined to its latest app.query_runs row so the
    reopened thread can restore the same result meta an in-session answer shows:
    engine/token/latency and — for admins — the step-by-step agent trace (the
    trace is persisted per run, not in messages.report, so it must be joined
    back here). The trace is gated to admins, matching /ask.
    """
    async with rls_connection(user.id) as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT m.id, m.role, m.content, m.sql_generated, m.report, "
                        "  m.created_at, qr.id AS run_id, qr.engine, qr.input_tokens, "
                        "  qr.output_tokens, qr.latency_ms, qr.trace "
                        "FROM app.messages m "
                        "JOIN app.conversations c ON c.id = m.conversation_id "
                        "LEFT JOIN LATERAL ("
                        "  SELECT id, engine, input_tokens, output_tokens, latency_ms, trace "
                        "  FROM app.query_runs qr WHERE qr.message_id = m.id "
                        "  ORDER BY qr.created_at DESC LIMIT 1"
                        ") qr ON true "
                        "WHERE m.conversation_id = :cid "
                        "  AND c.user_id = current_setting('app.current_user_id', true)::uuid "
                        "ORDER BY m.created_at ASC"
                    ),
                    {"cid": conversation_id},
                )
            )
            .mappings()
            .all()
        )
    is_admin = user.role == "admin"
    out: list[dict[str, Any]] = []
    for r in rows:
        d = {k: jsonable(v) for k, v in r.items()}
        # Only admins get the step-by-step trace, mirroring /ask.
        d["steps"] = d.pop("trace") or [] if is_admin else []
        out.append(d)
    return out


async def _open_conversation(
    user: CurrentUser, conversation_id: str | None, question: str
) -> tuple[str, str]:
    """tx1: ensure the conversation, record the user's message, mark agent started.

    Also returns the user's current app plan (free|plus|pro) — read fresh per
    question inside the same transaction, so a plan change applies to the very
    next answer without a re-login. The data-agent's page_plan(plan) gates how
    many answer pages this user gets (s10).
    """
    # s32 W3 (decision Q4): what gets STORED is scrubbed; what the agent answers
    # is the question as asked. Masking upstream would change the question.
    stored = scrub_text(question) or question
    async with rls_connection(user.id) as conn:
        plan = (
            await conn.execute(text("SELECT plan FROM app.users WHERE id = :uid"), {"uid": user.id})
        ).scalar() or "free"
        if conversation_id is None:
            conversation_id = str(
                (
                    await conn.execute(
                        text(
                            "INSERT INTO app.conversations (user_id, dataset_id, title) "
                            "VALUES (:uid, (SELECT id FROM app.datasets WHERE slug = :slug), "
                            ":title) RETURNING id"
                        ),
                        {"uid": user.id, "slug": DATASET_SLUG, "title": stored[:60]},
                    )
                ).scalar_one()
            )
        await conn.execute(
            text(
                "INSERT INTO app.messages (conversation_id, user_id, role, content) "
                "VALUES (:cid, :uid, 'user', :content)"
            ),
            {"cid": conversation_id, "uid": user.id, "content": stored},
        )
        await _log_event(conn, user.id, "agent_started", {"question": stored})
    return conversation_id, str(plan)


def _run_status(result: dict[str, Any]) -> str:
    """The audit status this answer actually deserves (s32 W1/W3).

    Until now this was the literal ``'success'`` for every run, so a
    guard-rejected question and a retried-then-stubbed answer were both recorded
    as clean successes — the two states an operator most needs to see were the
    two the audit trail hid. Order matters: an error is an error even if the run
    also degraded on the way there.
    """
    if result.get("error"):
        return "error"
    if result.get("degraded"):
        return "degraded"
    return "success"


async def _persist_answer(
    user: CurrentUser,
    channel: str,
    conversation_id: str,
    question: str,
    result: dict[str, Any],
    latency_ms: int,
    ttfp_ms: int | None = None,
) -> tuple[str, str]:
    """tx2: record the assistant's answer + audit run; return (message_id, run_id)."""
    engine = result.get("engine", "stub")
    report = result.get("report")
    status = _run_status(result)
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
                        "content": result.get("answer", ""),
                        "sql": result.get("sql"),
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
                        "sql_text, engine, row_count, latency_ms, status, error, input_tokens, "
                        "output_tokens, cache_read_tokens, cache_write_tokens, cost_usd, "
                        "degraded, attempts, ttfp_ms, otel_trace_id, trace, channel, "
                        "agent_version_id) "
                        "VALUES (:cid, :mid, :uid, "
                        "(SELECT id FROM app.datasets WHERE slug = :slug), :question, :sql, "
                        ":engine, :row_count, :lat, :status, :err, :in_tok, :out_tok, "
                        ":cache_read, :cache_write, :cost_usd, :degraded, :attempts, :ttfp, "
                        ":trace_id, CAST(:trace AS jsonb), :channel, :agent_version_id) "
                        "RETURNING id"
                    ),
                    {
                        "cid": conversation_id,
                        "mid": message_id,
                        "uid": user.id,
                        "slug": DATASET_SLUG,
                        # Scrubbed on the way into the audit trail, same as the
                        # message above — the two must not disagree.
                        "question": scrub_text(question) or question,
                        "sql": result.get("sql"),
                        "engine": engine,
                        "row_count": int(result.get("row_count", 0)),
                        "lat": latency_ms,
                        "status": status,
                        "err": result.get("error"),
                        "in_tok": result.get("input_tokens"),
                        "out_tok": result.get("output_tokens"),
                        # s32 W2: the cache split and the priced cost the agent
                        # computed, promoted out of the trace jsonb into columns
                        # so the deck's cost rollup is a sum, not a jsonb walk.
                        "cache_read": result.get("cache_read_tokens"),
                        "cache_write": result.get("cache_write_tokens"),
                        "cost_usd": result.get("cost_usd"),
                        "degraded": bool(result.get("degraded")),
                        "attempts": int(result.get("attempts") or 1),
                        # s32 W2: the felt latency, measured separately from the
                        # extract-bound full-answer time — SLO-B grades this.
                        "ttfp": ttfp_ms,
                        # The Logfire deep-link for this run (s32 W2): the deck's
                        # slow-ask table hands this id straight to the microscope.
                        "trace_id": current_trace_id(),
                        "trace": _json(result.get("steps") or []),
                        "channel": channel,
                        # Which build answered this (s24 M1). None when the agent
                        # cannot be reached — provenance never blocks an answer.
                        "agent_version_id": await current_agent_version_id(conn),
                    },
                )
            ).scalar_one()
        )
        await _log_event(
            conn,
            user.id,
            "agent_answered",
            {"latency_ms": latency_ms, "engine": engine, "status": status},
        )
        # s32 W3: a refusal is its own signal, counted on the deck separately
        # from failures. Emitted as an event rather than a query_runs column so
        # every surface that can deny (chat, editor) feeds one counter.
        if result.get("denied"):
            await _log_event(
                conn,
                user.id,
                "security_denied",
                {"surface": "chat", "reason": str(result.get("error"))[:200]},
            )
    return message_id, run_id


async def _retitle_conversation(user: CurrentUser, conversation_id: str, question: str) -> None:
    """Replace a new conversation's raw-question placeholder title with a short
    agent-generated summary (s17 E1). Best-effort and off the answer's critical
    path (runs as a background task) — any failure just keeps the fallback title."""
    try:
        title = (await title_agent(question)).strip()
    except Exception as exc:  # noqa: BLE001 — titling is cosmetic, never surface it
        print(f"[backend-api] conversation retitle skipped: {exc}")
        return
    if not title:
        return
    try:
        async with rls_connection(user.id) as conn:
            await conn.execute(
                text("UPDATE app.conversations SET title = :t WHERE id = :cid"),
                {"t": title[:120], "cid": conversation_id},
            )
    except Exception as exc:  # noqa: BLE001
        print(f"[backend-api] conversation retitle write failed: {exc}")


def _build_response(
    conversation_id: str,
    message_id: str,
    run_id: str,
    result: dict[str, Any],
    latency_ms: int,
    is_admin: bool,
) -> AskResponse:
    return AskResponse(
        conversation_id=conversation_id,
        message_id=message_id,
        run_id=run_id,
        answer=result.get("answer", ""),
        sql=result.get("sql"),
        columns=result.get("columns", []),
        rows=result.get("rows", []),
        row_count=int(result.get("row_count", 0)),
        chart=result.get("chart"),
        engine=result.get("engine", "stub"),
        input_tokens=result.get("input_tokens"),
        output_tokens=result.get("output_tokens"),
        latency_ms=latency_ms,
        degraded=bool(result.get("degraded")),
        # Only admins get the step-by-step trace in chat; it's still persisted for all runs.
        steps=(result.get("steps") or []) if is_admin else [],
        report=result.get("report"),
        pages=result.get("pages"),
    )


def _clean_question(raw: str) -> str:
    """Validate the one free-text field a user controls (s32 W3).

    Length only — the PII scrub happens at *persistence*, not here, so the agent
    still answers the question the user actually asked. Masking before the agent
    sees it would turn "what about 0412 345 678" into a different question; the
    point is that the masked version is what gets stored and traced.
    """
    question = raw.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question must not be empty")
    if len(question) > MAX_QUESTION_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"Question is too long ({len(question)} chars, max {MAX_QUESTION_CHARS})",
        )
    return question


def _degraded_result(reason: str) -> dict[str, Any]:
    """The answer a user gets when the agent could not be reached (s32 W1).

    Before this, an unreachable agent was a raw 502 and a red bubble. The hop is
    already retried (agent_client), so arriving here means the agent is genuinely
    down — and the honest response is a plain sentence plus a recorded degraded
    run, not a status code the UI has to guess at. Deterministic and instant, so
    an outage fails fast rather than slowly.
    """
    return {
        "answer": (
            "I couldn't reach the analysis service just now, so there's no answer to give "
            "yet. It's usually brief — try the same question again in a moment."
        ),
        "engine": "unavailable",
        "degraded": True,
        "error": reason,
        "steps": [{"kind": "fallback", "status": "error", "error": reason, "to": "degraded"}],
    }


@router.post("/ask", response_model=AskResponse)
async def ask(
    body: AskRequest,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(get_current_user),
    channel: str = Depends(get_channel),
) -> AskResponse:
    question = _clean_question(body.question)
    await check_daily_llm_cap(user)

    is_new = body.conversation_id is None
    conversation_id, plan = await _open_conversation(user, body.conversation_id, question)
    # Delegate to the agent (its own connection enforces the same RLS).
    started = time.perf_counter()
    try:
        result = await ask_agent(
            question=question,
            user_id=user.id,
            role=user.role,
            plan=plan,
            dataset_slug=DATASET_SLUG,
        )
    except httpx.HTTPError as exc:  # noqa: BLE001
        async with rls_connection(user.id) as conn:
            await _log_event(conn, user.id, "agent_error", {"error": str(exc)})
        result = _degraded_result(f"agent unavailable: {exc}")
    latency_ms = int((time.perf_counter() - started) * 1000)

    message_id, run_id = await _persist_answer(
        user, channel, conversation_id, question, result, latency_ms
    )
    # Summarise the first question into a short sidebar title after the response
    # is sent, so it never adds to the answer's latency (s17 E1).
    if is_new:
        background_tasks.add_task(_retitle_conversation, user, conversation_id, question)
    return _build_response(
        conversation_id, message_id, run_id, result, latency_ms, user.role == "admin"
    )


def _sse(event: str, data: dict[str, Any] | str) -> str:
    payload = data if isinstance(data, str) else json.dumps(data)
    return f"event: {event}\ndata: {payload}\n\n"


@router.post("/ask/stream")
async def ask_stream(
    body: AskRequest,
    user: CurrentUser = Depends(get_current_user),
    channel: str = Depends(get_channel),
) -> StreamingResponse:
    """SSE variant of /ask: relays the agent's live step events (``progress``
    frames) as it works, then persists the answer and emits one ``result`` frame.
    Same auth, persistence and payload as /ask — the frontend shows a running
    step list instead of a silent spinner. ``status`` frames are heartbeats.
    """
    # Enforce the cap before the stream opens so the client gets a clean 429.
    await check_daily_llm_cap(user)

    # Populated by gen() for a new conversation; the background task below retitles
    # it once the stream has closed (never delays the streamed answer, s17 E1).
    retitle: dict[str, str] = {}

    async def gen() -> AsyncIterator[str]:
        try:
            question = _clean_question(body.question)
        except HTTPException as exc:
            yield _sse("error", {"detail": exc.detail, "status": exc.status_code})
            return
        conversation_id, plan = await _open_conversation(user, body.conversation_id, question)
        started = time.perf_counter()
        yield _sse("status", {"state": "started"})

        result: dict[str, Any] | None = None
        # Time to first page: the moment the user stops looking at a spinner and
        # starts reading an answer. Measured here, at the frame the client
        # actually renders, rather than inferred from the agent's own timings —
        # it is what SLO-B grades (s32 W2).
        ttfp_ms: int | None = None
        try:
            async for ev in ask_agent_stream(
                question=question,
                user_id=user.id,
                role=user.role,
                plan=plan,
                dataset_slug=DATASET_SLUG,
            ):
                name = ev["event"]
                if name == "progress":
                    yield _sse("progress", ev["data"])
                elif name in ("plan", "page"):
                    # s10 streaming pages: relay the page plan + each finished
                    # page (Template Studio Page JSON) verbatim to the client.
                    # The plan frame is deliberately NOT the TTFP mark — it is
                    # ghost placeholders, not content; the first COMPLETE page is.
                    if (
                        name == "page"
                        and ttfp_ms is None
                        and ev["data"].get("status") == "complete"
                    ):
                        ttfp_ms = int((time.perf_counter() - started) * 1000)
                    yield _sse(name, ev["data"])
                elif name == "status":
                    yield _sse(
                        "status",
                        {"state": "working", "elapsed_s": int(time.perf_counter() - started)},
                    )
                elif name == "result":
                    result = ev["data"]
                elif name == "error":
                    async with rls_connection(user.id) as conn:
                        await _log_event(conn, user.id, "agent_error", ev["data"])
                    yield _sse("error", ev["data"])
                    return
        except httpx.HTTPError as exc:  # noqa: BLE001
            async with rls_connection(user.id) as conn:
                await _log_event(conn, user.id, "agent_error", {"error": str(exc)})
            # s32 W1: a degraded RESULT frame, not a bare error. The client has a
            # sentence to render and the run is recorded as degraded, so the
            # outage shows up on the deck instead of only in a browser console.
            result = _degraded_result(f"agent unavailable: {exc}")

        if result is None:
            result = _degraded_result("agent stream ended without a result")

        latency_ms = int((time.perf_counter() - started) * 1000)
        message_id, run_id = await _persist_answer(
            user, channel, conversation_id, question, result, latency_ms, ttfp_ms
        )
        if body.conversation_id is None:
            retitle["cid"] = conversation_id
            retitle["q"] = question
        response = _build_response(
            conversation_id, message_id, run_id, result, latency_ms, user.role == "admin"
        )
        yield _sse("result", response.model_dump_json())

    async def _bg_retitle() -> None:
        if retitle:
            await _retitle_conversation(user, retitle["cid"], retitle["q"])

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        background=BackgroundTask(_bg_retitle),
    )
