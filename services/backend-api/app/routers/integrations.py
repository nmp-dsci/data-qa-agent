"""Webhook and Slack front doors (s35 rungs 1-2).

Both are thin: authenticate, hand the question to ``run_question`` (the same
pipeline /ask uses), deliver the answer. Neither reimplements the cap, the RLS
scoping or the audit write — that is the whole point of the shared helper.

Both are also asynchronous, for the same reason: a real question can take well
over a minute, Slack demands an acknowledgement inside 3 seconds, and no sane
webhook caller holds a connection open that long. So both ack immediately and
deliver the answer out of band.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.parse import parse_qs

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from ..auth import CurrentUser, service_key_user
from ..config import settings
from ..db import rls_connection
from ..integrations.signing import now_timestamp, sign_payload, verify_slack
from .ask import run_question

log = logging.getLogger(__name__)
router = APIRouter(tags=["integrations"])

# Slack renders a code block badly past a few thousand characters, and the
# answer field is a sentence or two by design.
SLACK_MAX_CHARS = 3_000
CALLBACK_TIMEOUT_S = 15.0


# ---------------------------------------------------------------------------
# Rung 1 — generic webhook
# ---------------------------------------------------------------------------
class WebhookAsk(BaseModel):
    question: str
    callback_url: str | None = Field(
        default=None,
        description="Where to POST the answer. Omit to fire-and-forget (the run is still audited).",
    )
    reference: str | None = Field(
        default=None, description="Echoed back in the callback so the caller can correlate it."
    )


@router.post("/integrations/webhook/ask", status_code=202)
async def webhook_ask(
    body: WebhookAsk,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(service_key_user("webhook")),
) -> dict[str, Any]:
    """Accept a question, answer it out of band, POST the result back.

    202 rather than 200: the answer genuinely isn't ready yet, and saying so is
    more honest than holding the socket for two minutes.
    """
    background_tasks.add_task(
        _deliver_webhook, user, body.question, body.callback_url, body.reference
    )
    return {"status": "accepted", "reference": body.reference}


async def _deliver_webhook(
    user: CurrentUser, question: str, callback_url: str | None, reference: str | None
) -> None:
    try:
        answer = await run_question(user, question, channel="webhook")
        payload = {
            "reference": reference,
            "question": question,
            "answer": answer.answer,
            "sql": answer.sql,
            "row_count": answer.row_count,
            "run_id": answer.run_id,
            "degraded": answer.degraded,
        }
    except HTTPException as exc:
        # A cap hit or a validation failure is a real outcome the caller needs,
        # not something to swallow into a silent non-delivery.
        payload = {"reference": reference, "question": question, "error": exc.detail}
    except Exception:
        log.exception("webhook question failed")
        payload = {"reference": reference, "question": question, "error": "internal error"}

    if not callback_url:
        return
    raw = json.dumps(payload).encode()
    ts = now_timestamp()
    headers = {
        "Content-Type": "application/json",
        "X-DataPilot-Timestamp": ts,
        # Signed with the caller's own key secret? No — we don't have it (only a
        # hash). The agent shared token is the one secret both ends can hold, so
        # it doubles as the callback signing key. Documented in the README.
        "X-DataPilot-Signature": sign_payload(settings.agent_shared_token, raw, ts),
    }
    try:
        async with httpx.AsyncClient(timeout=CALLBACK_TIMEOUT_S) as client:
            await client.post(callback_url, content=raw, headers=headers)
    except httpx.HTTPError:
        # The answer is already persisted in query_runs; a dead callback URL is
        # the caller's problem to notice, not a reason to lose the run.
        log.warning("callback delivery failed for %s", callback_url)


# ---------------------------------------------------------------------------
# Rung 2 — Slack slash command
# ---------------------------------------------------------------------------
@router.post("/integrations/slack/command")
async def slack_command(
    request: Request,
    background_tasks: BackgroundTasks,
    x_slack_signature: str | None = Header(default=None),
    x_slack_request_timestamp: str | None = Header(default=None),
) -> dict[str, Any]:
    """Slack slash command: verify, ack inside 3s, answer via response_url.

    Note this does NOT use ``service_key_user`` — Slack authenticates by signing
    its own requests, so the signature proves the request and the configured
    service account decides what it may see. Two separate jobs.
    """
    if not settings.slack_signing_secret:
        # Same posture as the ops ingest endpoints: unconfigured means the path
        # does not exist, rather than existing in a weakened form.
        raise HTTPException(status_code=404, detail="Slack integration is not enabled")

    # RAW body, before anything parses it. Re-serialising a parsed form and
    # signing that is the classic intermittent-failure bug here.
    raw = await request.body()
    if not verify_slack(
        settings.slack_signing_secret,
        raw,
        x_slack_request_timestamp or "",
        x_slack_signature or "",
        now=time.time(),
    ):
        raise HTTPException(status_code=401, detail="Bad Slack signature")

    # parse_qs rather than a hand-rolled split: Slack's payload is
    # percent-encoded and a question routinely contains '=' and '&'.
    form = parse_qs(raw.decode())
    question = (form.get("text", [""])[0]).strip()
    response_url = form.get("response_url", [""])[0]
    slack_user = form.get("user_id", [""])[0]
    slack_user_name = form.get("user_name", [""])[0]
    slack_channel = form.get("channel_id", [""])[0]

    if not question:
        return {
            "response_type": "ephemeral",
            "text": (
                "Ask me something about the property data — e.g. `/datapilot median rent in 2077`"
            ),
        }

    user = await _slack_service_account()
    background_tasks.add_task(
        _deliver_slack,
        user,
        question,
        response_url,
        {"user_id": slack_user, "user_name": slack_user_name, "channel_id": slack_channel},
    )
    # Slack shows this immediately; the real answer replaces it via response_url.
    return {"response_type": "ephemeral", "text": f"Working on it — _{question}_"}


async def _slack_service_account() -> CurrentUser:
    """The single bot identity behind the Slack surface.

    v1 is a bot with its own dataset grants: channel membership is the access
    boundary (s35). Nothing the request says can change which identity this is —
    it is looked up by surface, not supplied by the caller.
    """
    async with rls_connection(None) as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT u.id, u.username, u.email, u.role "
                        "FROM app.service_accounts sa JOIN app.users u ON u.id = sa.user_id "
                        "WHERE sa.surface = 'slack' AND sa.revoked_at IS NULL "
                        "ORDER BY sa.created_at LIMIT 1"
                    )
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        raise HTTPException(status_code=503, detail="No active Slack service account is configured")
    return CurrentUser(
        id=str(row["id"]), username=row["username"], email=row["email"], role=row["role"]
    )


async def _deliver_slack(
    user: CurrentUser, question: str, response_url: str, asker: dict[str, str]
) -> None:
    try:
        answer = await run_question(user, question, channel="slack")
        text_out = answer.answer or "No answer came back for that one."
        # Authorisation is the bot's; accountability is still the human's. The
        # asker doesn't change what can be read, so it is recorded rather than
        # enforced — but "who asked this" must stay answerable.
        await _audit_slack_asker(user, asker, answer.run_id)
    except HTTPException as exc:
        text_out = f"Couldn't answer that: {exc.detail}"
    except Exception:
        log.exception("slack question failed")
        text_out = "Something went wrong answering that. It's been logged."

    if not response_url:
        return
    body = {"response_type": "in_channel", "text": text_out[:SLACK_MAX_CHARS]}
    try:
        async with httpx.AsyncClient(timeout=CALLBACK_TIMEOUT_S) as client:
            await client.post(response_url, json=body)
    except httpx.HTTPError:
        log.warning("slack response_url delivery failed")


async def _audit_slack_asker(user: CurrentUser, asker: dict[str, str], run_id: str) -> None:
    async with rls_connection(user.id) as conn:
        await conn.execute(
            text(
                "INSERT INTO app.events (user_id, event_type, payload) "
                "VALUES (:uid, 'slack_question', CAST(:payload AS jsonb))"
            ),
            {"uid": user.id, "payload": json.dumps({**asker, "run_id": run_id})},
        )
