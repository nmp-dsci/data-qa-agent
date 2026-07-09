"""Feedback capture + admin triage — the learning loop's front door (§06/§07).

Any user can leave element-anchored feedback on a report (click-to-annotate).
Admins review it beside the re-rendered report, batch-promote captures into
app.eval_cases, or reclassify them as user memory / dismiss. eval_cases status
is toggleable stale<->active.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from ..agent_client import ask_agent
from ..auth import CurrentUser, get_current_user, require_admin
from ..db import jsonable, rls_connection

router = APIRouter(tags=["feedback"])

# A case that stays stale this many consecutive runs is auto-archived (§06).
STALE_ARCHIVE_CYCLES = 3


def _element_still_present(report: dict[str, Any] | None, target_kind: str, snapshot: dict) -> bool:
    """Best-effort staleness check: is the element the feedback judged still there?

    Materially-changed means the element (identified by its heading/label) no
    longer appears in a freshly generated report — the derived eval is then
    flagged stale rather than silently grading the new behaviour.
    """
    if not report:
        return False
    if target_kind == "report":
        return bool(report.get("summary"))
    key = str(snapshot.get("heading") or snapshot.get("label") or "").strip().lower()
    if not key:
        # No identifying text captured — treat presence of the section as enough.
        return bool(report.get(f"{target_kind}s") or report.get("summary"))
    section = report.get(f"{target_kind}s", [])
    for el in section:
        text = str(el.get("heading") or el.get("label") or "").strip().lower()
        if text and (text == key or key in text or text in key):
            return True
    return False


class FeedbackIn(BaseModel):
    message_id: str
    rating: int  # -1 or 1
    accurate: bool | None = None
    issue_flag: bool = False
    comment: str | None = None
    target_kind: str  # report|headline|insight|profile|chart|query
    target_ref: str  # element_id, e.g. 'insight:2'
    target_snapshot: dict[str, Any] = {}
    target_render_html: str = ""
    report_snapshot: dict[str, Any] = {}
    knowledge_version: str = ""
    knowledge_pages: list[str] = []
    client_context: dict[str, Any] = {}


@router.post("/feedback", status_code=201)
async def submit_feedback(
    body: FeedbackIn, user: CurrentUser = Depends(get_current_user)
) -> dict[str, str]:
    if body.rating not in (-1, 1):
        raise HTTPException(status_code=400, detail="rating must be -1 or 1")
    async with rls_connection(user.id) as conn:
        fid = (
            await conn.execute(
                text(
                    "INSERT INTO app.answer_feedback "
                    "(message_id, user_id, rating, accurate, issue_flag, comment, "
                    " target_kind, target_ref, target_snapshot, target_render_html, "
                    " report_snapshot, knowledge_version, knowledge_pages, client_context) "
                    "VALUES (:mid, :uid, :rating, :accurate, :issue_flag, :comment, "
                    " :kind, :ref, CAST(:snap AS jsonb), :html, CAST(:report AS jsonb), "
                    " :kv, CAST(:pages AS jsonb), CAST(:ctx AS jsonb)) RETURNING id"
                ),
                {
                    "mid": body.message_id,
                    "uid": user.id,
                    "rating": body.rating,
                    "accurate": body.accurate,
                    "issue_flag": body.issue_flag,
                    "comment": body.comment,
                    "kind": body.target_kind,
                    "ref": body.target_ref,
                    "snap": json.dumps(body.target_snapshot),
                    "html": body.target_render_html,
                    "report": json.dumps(body.report_snapshot),
                    "kv": body.knowledge_version,
                    "pages": json.dumps(body.knowledge_pages),
                    "ctx": json.dumps(body.client_context),
                },
            )
        ).scalar_one()
    return {"status": "ok", "id": str(fid)}


@router.get("/admin/feedback")
async def list_feedback(
    limit: int = 100, admin: CurrentUser = Depends(require_admin)
) -> list[dict[str, Any]]:
    """Feedback joined to its message (incl. the stored report to re-render) + question."""
    async with rls_connection(admin.id) as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT f.id, f.rating, f.comment, f.target_kind, f.target_ref, "
                        "  f.accurate, f.issue_flag, f.target_snapshot, f.target_render_html, "
                        "  f.report_snapshot, f.client_context, "
                        "  f.knowledge_version, f.knowledge_pages, "
                        "  f.scope, f.status, f.created_at, u.username, f.message_id, "
                        "  m.report, "
                        "  (SELECT content FROM app.messages q "
                        "     WHERE q.conversation_id = m.conversation_id AND q.role = 'user' "
                        "       AND q.created_at <= m.created_at "
                        "     ORDER BY q.created_at DESC LIMIT 1) AS question "
                        "FROM app.answer_feedback f "
                        "JOIN app.users u ON u.id = f.user_id "
                        "JOIN app.messages m ON m.id = f.message_id "
                        "ORDER BY f.created_at DESC LIMIT :lim"
                    ),
                    {"lim": limit},
                )
            )
            .mappings()
            .all()
        )
    return [{k: jsonable(v) for k, v in r.items()} for r in rows]


class PromoteIn(BaseModel):
    feedback_ids: list[str]


@router.post("/admin/feedback/promote")
async def promote_feedback(
    body: PromoteIn, admin: CurrentUser = Depends(require_admin)
) -> dict[str, Any]:
    """Batch-promote feedback into eval cases (admin decision, §06)."""
    created = 0
    async with rls_connection(admin.id) as conn:
        for fid in body.feedback_ids:
            row = (
                (
                    await conn.execute(
                        text(
                            "SELECT f.comment, f.rating, f.accurate, f.issue_flag, "
                            "  f.target_kind, f.target_snapshot, f.user_id, "
                            "  f.knowledge_version, m.conversation_id, m.created_at AS m_at "
                            "FROM app.answer_feedback f JOIN app.messages m ON m.id = f.message_id "
                            "WHERE f.id = :fid"
                        ),
                        {"fid": fid},
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                continue
            question = (
                await conn.execute(
                    text(
                        "SELECT content FROM app.messages "
                        "WHERE conversation_id = :cid AND role = 'user' AND created_at <= :at "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"cid": row["conversation_id"], "at": row["m_at"]},
                )
            ).scalar()
            expectation = row["comment"] or (
                "This element was rated helpful — keep it."
                if row["rating"] == 1
                else "This element was rated unhelpful — improve or drop it."
            )
            if row["accurate"] is False or row["issue_flag"]:
                expectation = (
                    f"{expectation} Verify the reported numbers; the user flagged numeric "
                    "accuracy as questionable."
                )
            await conn.execute(
                text(
                    "INSERT INTO app.eval_cases "
                    "(feedback_id, question, expectation, target_kind, target_snapshot, "
                    " knowledge_version) "
                    "VALUES (:fid, :q, :exp, :kind, CAST(:snap AS jsonb), :kv)"
                ),
                {
                    "fid": fid,
                    "q": question or "(question unavailable)",
                    "exp": expectation,
                    "kind": row["target_kind"],
                    "snap": json.dumps(row["target_snapshot"]),
                    "kv": row["knowledge_version"],
                },
            )
            await conn.execute(
                text("UPDATE app.answer_feedback SET status = 'promoted_to_eval' WHERE id = :fid"),
                {"fid": fid},
            )
            created += 1
    return {"status": "ok", "created": created}


class TriageIn(BaseModel):
    action: str  # 'user_memory' | 'dismiss'


@router.post("/admin/feedback/{feedback_id}/triage")
async def triage_feedback(
    feedback_id: str, body: TriageIn, admin: CurrentUser = Depends(require_admin)
) -> dict[str, str]:
    if body.action not in ("user_memory", "dismiss"):
        raise HTTPException(status_code=400, detail="action must be user_memory or dismiss")
    status = "user_memory" if body.action == "user_memory" else "dismissed"
    scope = "user_memory" if body.action == "user_memory" else "knowledge"
    async with rls_connection(admin.id) as conn:
        row = None
        if body.action == "user_memory":
            row = (
                (
                    await conn.execute(
                        text(
                            "SELECT user_id, comment, target_kind, target_snapshot "
                            "FROM app.answer_feedback WHERE id = :fid"
                        ),
                        {"fid": feedback_id},
                    )
                )
                .mappings()
                .first()
            )
        await conn.execute(
            text("UPDATE app.answer_feedback SET status = :st, scope = :sc WHERE id = :fid"),
            {"st": status, "sc": scope, "fid": feedback_id},
        )
    if row is not None:
        memory = _feedback_memory_text(row)
        async with rls_connection(str(row["user_id"])) as conn:
            await conn.execute(
                text(
                    "INSERT INTO app.user_memories (user_id, kind, content) "
                    "VALUES (:uid, 'preference', :content)"
                ),
                {"uid": row["user_id"], "content": memory},
            )
    return {"status": "ok"}


def _feedback_memory_text(row: Any) -> str:
    snapshot = row["target_snapshot"] or {}
    label = snapshot.get("heading") or snapshot.get("label") or row["target_kind"]
    comment = (row["comment"] or "").strip()
    if comment:
        return f"User preference from feedback on {label}: {comment}"
    return f"User marked {label} as personally relevant feedback."


@router.get("/admin/eval-cases")
async def list_eval_cases(admin: CurrentUser = Depends(require_admin)) -> list[dict[str, Any]]:
    async with rls_connection(admin.id) as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT id, question, expectation, target_kind, knowledge_version, "
                        "  status, stale_cycles, created_at, updated_at "
                        "FROM app.eval_cases ORDER BY created_at DESC"
                    )
                )
            )
            .mappings()
            .all()
        )
    return [{k: jsonable(v) for k, v in r.items()} for r in rows]


class ToggleIn(BaseModel):
    status: str  # 'active' | 'stale' | 'archived'


@router.post("/admin/eval-cases/run-staleness")
async def run_staleness(admin: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Re-ask each active eval case and flag/auto-archive stale ones (§06).

    Compares the freshly generated report's corresponding element against the
    stored snapshot. Present → reset stale_cycles. Gone/changed → increment;
    at STALE_ARCHIVE_CYCLES the case auto-archives. Returns a run summary.
    """
    checked = 0
    flagged = 0
    archived = 0
    async with rls_connection(admin.id) as conn:
        cases = (
            (
                await conn.execute(
                    text(
                        "SELECT id, question, target_kind, target_snapshot, stale_cycles "
                        "FROM app.eval_cases WHERE status IN ('active', 'stale')"
                    )
                )
            )
            .mappings()
            .all()
        )
    for case in cases:
        checked += 1
        try:
            result = await ask_agent(
                question=case["question"],
                user_id=admin.id,
                role=admin.role,
                # Staleness replays need the full report (insights included) to
                # compare stored elements, regardless of the admin's own plan.
                plan="pro",
                dataset_slug="nsw_sales",
            )
        except Exception:  # noqa: BLE001 — a run failure just skips this case this cycle
            continue
        report = result.get("report")
        present = _element_still_present(report, case["target_kind"], case["target_snapshot"] or {})
        async with rls_connection(admin.id) as conn:
            if present:
                await conn.execute(
                    text(
                        "UPDATE app.eval_cases SET status = 'active', stale_cycles = 0, "
                        "updated_at = now() WHERE id = :cid"
                    ),
                    {"cid": case["id"]},
                )
            else:
                cycles = case["stale_cycles"] + 1
                new_status = "archived" if cycles >= STALE_ARCHIVE_CYCLES else "stale"
                if new_status == "archived":
                    archived += 1
                else:
                    flagged += 1
                await conn.execute(
                    text(
                        "UPDATE app.eval_cases SET status = :st, stale_cycles = :sc, "
                        "updated_at = now() WHERE id = :cid"
                    ),
                    {"st": new_status, "sc": cycles, "cid": case["id"]},
                )
    return {"checked": checked, "flagged_stale": flagged, "archived": archived}


@router.post("/admin/eval-cases/{case_id}/status")
async def set_eval_case_status(
    case_id: str, body: ToggleIn, admin: CurrentUser = Depends(require_admin)
) -> dict[str, str]:
    if body.status not in ("active", "stale", "archived"):
        raise HTTPException(status_code=400, detail="invalid status")
    async with rls_connection(admin.id) as conn:
        await conn.execute(
            text(
                "UPDATE app.eval_cases SET status = :st, "
                "stale_cycles = CASE WHEN :st = 'active' THEN 0 ELSE stale_cycles END, "
                "updated_at = now() WHERE id = :cid"
            ),
            {"st": body.status, "cid": case_id},
        )
    return {"status": "ok"}
