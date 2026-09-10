"""Golden Answer (Builder) — author & manage the eval goldens (s14 E1).

Admin-gated CRUD over the *authored* rows of app.eval_cases (source='authored').
Each golden carries the three executable stages — golden_sql (extract),
golden_sandbox (the run_analysis script) and golden_report (PagesEnvelope) —
plus dataset / tier / as_user / tags / holdout. This is the backend for the
Golden Answer (Builder) tab; a `ready` golden is the 100/100 benchmark the eval
runner scores the agent against. Feedback-promoted rows (source='feedback', §06)
share the table but are managed by feedback.py.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text

from ..agent_client import fetch_skills, prep_golden
from ..auth import CurrentUser, admin_or_demo_read, require_admin
from ..config import settings
from ..db import jsonable, rls_connection
from ..limits import check_demo_ip_rate

router = APIRouter(tags=["goldens"])


async def _resolve_user_id(as_user: str | None, admin: CurrentUser) -> str:
    """The user id the agent runs its RLS extract under, from a golden's ``as_user``.

    A golden stores ``as_user`` as a seeded *username* ("user1"/"user2"/"admin")
    or blank — but the agent and Postgres RLS key on the user *id* (a uuid). A raw
    username passed straight through fails every build/prep/draft with
    ``invalid input syntax for type uuid: "user1"``, which is what made curating a
    golden's answer impossible. So: blank → the admin; an id → itself; a known
    username → its id.

    An *unknown* username is rejected (400), not silently run as the admin: a
    typo would otherwise build the extract under full-admin RLS visibility while
    still claiming the seeded identity, masking a governance mismatch — and eval
    replay (dev-login by username) fails loudly on the same typo, so curation and
    eval must behave the same way.
    """
    if not as_user:
        return admin.id
    try:
        uuid.UUID(str(as_user))
        return str(as_user)
    except ValueError:
        pass
    async with rls_connection(admin.id) as conn:
        row = (
            await conn.execute(text("SELECT id FROM app.users WHERE username = :u"), {"u": as_user})
        ).scalar()
    if row is None:
        raise HTTPException(status_code=400, detail=f"unknown as_user: {as_user!r}")
    return str(jsonable(row))


# Column names below are fixed literals / driven by the Pydantic models — never
# raw user strings — so building them into the SQL text is injection-safe.
_LIST_COLS = (
    "id, dataset, tier, question, as_user, tags, holdout, authoring_status, "
    "(golden_sql IS NOT NULL) AS has_sql, (golden_sandbox IS NOT NULL) AS has_sandbox, "
    "(golden_data IS NOT NULL) AS has_data, (golden_report IS NOT NULL) AS has_report, "
    "(grader->>'kind') AS grader_kind, "
    "created_at, updated_at"
)
_FULL_COLS = (
    "id, source, dataset, tier, question, expectation, as_user, tags, holdout, "
    "authoring_status, golden_sql, golden_sandbox, golden_data, golden_report, "
    "golden_objects, grader, created_at, updated_at"
)
_JSONB_COLS = {"tags", "golden_data", "golden_report", "golden_objects", "grader"}


class GoldenIn(BaseModel):
    question: str
    dataset: str | None = None
    tier: str | None = None
    as_user: str | None = None
    tags: list[str] = []
    holdout: bool = False
    authoring_status: str = "draft"
    golden_sql: str | None = None
    golden_sandbox: str | None = None
    golden_data: Any | None = None
    golden_report: Any | None = None
    golden_objects: Any | None = None
    grader: Any | None = None
    expectation: str | None = None


class GoldenPatch(BaseModel):
    question: str | None = None
    dataset: str | None = None
    tier: str | None = None
    as_user: str | None = None
    tags: list[str] | None = None
    holdout: bool | None = None
    authoring_status: str | None = None
    golden_sql: str | None = None
    golden_sandbox: str | None = None
    golden_data: Any | None = None
    golden_report: Any | None = None
    golden_objects: Any | None = None
    grader: Any | None = None
    expectation: str | None = None


def _jsonb_param(value: Any) -> str | None:
    return json.dumps(value) if value is not None else None


@router.get("/admin/eval-goldens")
async def list_goldens(
    request: Request,
    dataset: str | None = None,
    admin: CurrentUser = Depends(admin_or_demo_read),
) -> list[dict[str, Any]]:
    """List authored goldens, newest first; optionally scoped to one dataset."""
    check_demo_ip_rate(request, "admin_read", settings.demo_rate_admin_read_per_min)
    clause = "WHERE source = 'authored'"
    params: dict[str, Any] = {}
    if dataset:
        clause += " AND dataset = :dataset"
        params["dataset"] = dataset
    async with rls_connection(admin.id) as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        f"SELECT {_LIST_COLS} FROM app.eval_cases {clause} ORDER BY created_at DESC"
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
    return [{k: jsonable(v) for k, v in r.items()} for r in rows]


# Declared before /{golden_id} so "skills" isn't captured as a golden id.
@router.get("/admin/eval-goldens/skills")
async def skills(
    request: Request, admin: CurrentUser = Depends(admin_or_demo_read)
) -> dict[str, Any]:
    """Sandbox skill catalog for the Golden Examples tab (available skills)."""
    check_demo_ip_rate(request, "admin_read", settings.demo_rate_admin_read_per_min)
    return await fetch_skills()


class OrdinalIn(BaseModel):
    dataset: str
    column: str
    ordered_values: list[str]


# Declared before /{golden_id} so "ordinals" isn't captured as a golden id.
@router.get("/admin/eval-goldens/ordinals")
async def list_ordinals(
    request: Request,
    dataset: str,
    admin: CurrentUser = Depends(admin_or_demo_read),
) -> list[dict[str, Any]]:
    """Ordinal band orders for a dataset (s23 data-knowledge panel). Each row is a
    ``(column, ordered_values)`` the chart lift sorts an ordinal x-axis by."""
    check_demo_ip_rate(request, "admin_read", settings.demo_rate_admin_read_per_min)
    async with rls_connection(admin.id) as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT o.column_name, o.ordered_values, o.updated_at "
                        "FROM app.dataset_ordinals o JOIN app.datasets d ON d.id = o.dataset_id "
                        "WHERE d.slug = :slug ORDER BY o.column_name"
                    ),
                    {"slug": dataset},
                )
            )
            .mappings()
            .all()
        )
    return [{k: jsonable(v) for k, v in r.items()} for r in rows]


@router.put("/admin/eval-goldens/ordinals")
async def upsert_ordinal(
    body: OrdinalIn, admin: CurrentUser = Depends(require_admin)
) -> dict[str, Any]:
    """Set the ordinal order for a ``(dataset, column)`` (s23). The data-agent picks
    it up on the next Run, so the curator edits + re-runs entirely in the Golden tab."""
    async with rls_connection(admin.id) as conn:
        result = await conn.execute(
            text(
                "INSERT INTO app.dataset_ordinals (dataset_id, column_name, ordered_values) "
                "SELECT id, :col, CAST(:vals AS jsonb) FROM app.datasets WHERE slug = :slug "
                "ON CONFLICT (dataset_id, column_name) "
                "DO UPDATE SET ordered_values = EXCLUDED.ordered_values, updated_at = now()"
            ),
            {"slug": body.dataset, "col": body.column, "vals": json.dumps(body.ordered_values)},
        )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="dataset not found")
    return {"status": "ok"}


@router.get("/admin/eval-goldens/{golden_id}")
async def get_golden(
    request: Request,
    golden_id: str,
    admin: CurrentUser = Depends(admin_or_demo_read),
) -> dict[str, Any]:
    """Full golden incl. all three stages, for the Builder / Evaluations tabs."""
    check_demo_ip_rate(request, "admin_read", settings.demo_rate_admin_read_per_min)
    async with rls_connection(admin.id) as conn:
        row = (
            (
                await conn.execute(
                    text(
                        f"SELECT {_FULL_COLS} FROM app.eval_cases "
                        "WHERE id = :id AND source = 'authored'"
                    ),
                    {"id": golden_id},
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        raise HTTPException(status_code=404, detail="golden not found")
    return {k: jsonable(v) for k, v in row.items()}


@router.post("/admin/eval-goldens")
async def create_golden(
    body: GoldenIn, admin: CurrentUser = Depends(require_admin)
) -> dict[str, Any]:
    """Create a new authored golden (starts in whatever authoring_status is sent)."""
    async with rls_connection(admin.id) as conn:
        new_id = (
            await conn.execute(
                text(
                    "INSERT INTO app.eval_cases "
                    "(source, question, expectation, dataset, tier, as_user, tags, holdout, "
                    " authoring_status, golden_sql, golden_sandbox, golden_data, golden_report, "
                    " golden_objects, grader) "
                    "VALUES ('authored', :q, :exp, :ds, :tier, :as_user, CAST(:tags AS jsonb), "
                    " :holdout, :status, :sql, :sandbox, "
                    " CAST(:data AS jsonb), CAST(:report AS jsonb), "
                    " CAST(COALESCE(:objects, '[]') AS jsonb), "
                    " CAST(COALESCE(:grader, '{}') AS jsonb)) "
                    "RETURNING id"
                ),
                {
                    "q": body.question,
                    "exp": body.expectation,
                    "ds": body.dataset,
                    "tier": body.tier,
                    "as_user": body.as_user,
                    "tags": json.dumps(body.tags),
                    "holdout": body.holdout,
                    "status": body.authoring_status,
                    "sql": body.golden_sql,
                    "sandbox": body.golden_sandbox,
                    "data": _jsonb_param(body.golden_data),
                    "report": _jsonb_param(body.golden_report),
                    "objects": _jsonb_param(body.golden_objects),
                    "grader": _jsonb_param(body.grader),
                },
            )
        ).scalar()
    return {"status": "ok", "id": jsonable(new_id)}


@router.put("/admin/eval-goldens/{golden_id}")
async def update_golden(
    golden_id: str, body: GoldenPatch, admin: CurrentUser = Depends(require_admin)
) -> dict[str, Any]:
    """Patch any subset of a golden's fields — the admin can edit every piece."""
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return {"status": "ok", "updated": 0}
    sets: list[str] = []
    params: dict[str, Any] = {"id": golden_id}
    for key, val in fields.items():
        if key in _JSONB_COLS:
            sets.append(f"{key} = CAST(:{key} AS jsonb)")
            params[key] = _jsonb_param(val)
        else:
            sets.append(f"{key} = :{key}")
            params[key] = val
    sets.append("updated_at = now()")
    async with rls_connection(admin.id) as conn:
        result = await conn.execute(
            text(
                f"UPDATE app.eval_cases SET {', '.join(sets)} "
                "WHERE id = :id AND source = 'authored'"
            ),
            params,
        )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="golden not found")
    return {"status": "ok", "updated": result.rowcount}


@router.delete("/admin/eval-goldens/{golden_id}")
async def delete_golden(
    golden_id: str, admin: CurrentUser = Depends(require_admin)
) -> dict[str, Any]:
    """Remove an authored golden (feedback-promoted rows are untouched)."""
    async with rls_connection(admin.id) as conn:
        result = await conn.execute(
            text("DELETE FROM app.eval_cases WHERE id = :id AND source = 'authored'"),
            {"id": golden_id},
        )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="golden not found")
    return {"status": "ok", "deleted": result.rowcount}


class PrepIn(BaseModel):
    sql: str
    code: str = ""
    # s18: named presentation objects to recompute against the same extract.
    objects: list[dict[str, Any]] = []
    # Run the extract under this user id's RLS; defaults to the admin.
    as_user: str | None = None


@router.post("/admin/eval-goldens/prep")
async def prep(body: PrepIn, admin: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Draft a golden's data via the data-agent: run the confirmed SQL (Goal A) and,
    if a sandbox script is given, the metrics (Goal B) — the same governed path the
    agent uses. Returns extract rows + the produced report/skills for the builder.
    """
    return await prep_golden(
        sql=body.sql,
        code=body.code,
        objects=body.objects,
        user_id=await _resolve_user_id(body.as_user, admin),
        role="user",
    )


def _sandbox_code_from_steps(steps: list[Any]) -> str:
    """Recover a *runnable* run_analysis script from the trace.

    A model turn's tool_calls carry ``args`` as a JSON string. The sandbox
    preloads ``df``/``pd``/``skills`` and blocks imports, so we take the first
    run_analysis pass and strip any import lines — a self-contained script that
    reproduces the answer and runs as-is, which the curator then edits. (Joining
    every pass produced un-runnable code: pass 1 kept ``import pandas`` and the
    passes aren't a single script.)
    """
    for step in steps:
        if not isinstance(step, dict) or step.get("kind") != "model":
            continue
        for call in step.get("tool_calls") or []:
            if not isinstance(call, dict) or call.get("name") != "run_analysis":
                continue
            args = call.get("args")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    continue
            code = args.get("code") if isinstance(args, dict) else None
            if code:
                lines = [
                    ln
                    for ln in str(code).splitlines()
                    if not re.match(r"\s*(import |from \S+ import )", ln)
                ]
                return "\n".join(lines).strip()
    return ""


class FromRunIn(BaseModel):
    run_id: str


@router.post("/admin/eval-goldens/from-run")
async def golden_from_run(
    body: FromRunIn, admin: CurrentUser = Depends(require_admin)
) -> dict[str, Any]:
    """Promote a stored chat answer into a draft golden — no agent re-run.

    Everything a golden needs was already captured when the question was asked:
    the question + extract SQL live on app.query_runs, the run_analysis script is
    inside that run's trace, and the rendered pages are on the answer's
    app.messages row. This endpoint is a pure read-and-insert — it copies those
    stored artifacts into a new authored golden (authoring_status='draft'). No
    LLM call, no SQL execution; the copied stages run only when the curator
    later presses Run in the Golden Examples editor.

    Idempotent: a ``run:<run_id>`` tag marks the source run, so pressing the
    button again returns the same golden with ``created=false`` instead of a
    duplicate.
    """
    run_tag = f"run:{body.run_id}"
    async with rls_connection(admin.id) as conn:
        existing = (
            await conn.execute(
                text(
                    "SELECT id FROM app.eval_cases "
                    "WHERE source = 'authored' AND tags @> CAST(:tag AS jsonb) LIMIT 1"
                ),
                {"tag": json.dumps([run_tag])},
            )
        ).scalar()
        if existing is not None:
            return {"status": "ok", "id": jsonable(existing), "created": False}

        run = (
            (
                await conn.execute(
                    text(
                        "SELECT r.question, r.sql_text, r.trace, m.report AS report, "
                        "COALESCE(d.slug, 'nsw_sales') AS dataset "
                        "FROM app.query_runs r "
                        "LEFT JOIN app.datasets d ON d.id = r.dataset_id "
                        "LEFT JOIN app.messages m ON m.id = r.message_id "
                        "WHERE r.id = :run_id"
                    ),
                    {"run_id": body.run_id},
                )
            )
            .mappings()
            .first()
        )
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")

        report = run["report"] if isinstance(run["report"], dict) else None
        pages = report.get("pages") if report else None
        golden_report = {"pages": pages} if pages else None
        # golden_sandbox: recover the run_analysis script from the stored trace
        # (empty for stub-engine runs that never called it — the curator authors
        # ② themselves in that case).
        sandbox = _sandbox_code_from_steps(run["trace"] or [])

        new_id = (
            await conn.execute(
                text(
                    "INSERT INTO app.eval_cases "
                    "(source, question, dataset, tier, as_user, tags, holdout, "
                    " authoring_status, golden_sql, golden_sandbox, golden_data, golden_report, "
                    " golden_objects) "
                    "VALUES ('authored', :q, :ds, 'T1', NULL, CAST(:tags AS jsonb), false, "
                    " 'draft', :sql, :sandbox, CAST(:data AS jsonb), CAST(:report AS jsonb), "
                    " '[]'::jsonb) "
                    "RETURNING id"
                ),
                {
                    "q": run["question"] or "",
                    "ds": run["dataset"],
                    "tags": json.dumps(["from-chat", run_tag]),
                    "sql": run["sql_text"],
                    "sandbox": sandbox or None,
                    "data": _jsonb_param(report),
                    "report": _jsonb_param(golden_report),
                },
            )
        ).scalar()
    return {"status": "ok", "id": jsonable(new_id), "created": True}
