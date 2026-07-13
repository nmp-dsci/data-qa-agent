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
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from ..agent_client import ask_agent, prep_golden
from ..auth import CurrentUser, require_admin
from ..db import jsonable, rls_connection

router = APIRouter(tags=["goldens"])

# Column names below are fixed literals / driven by the Pydantic models — never
# raw user strings — so building them into the SQL text is injection-safe.
_LIST_COLS = (
    "id, dataset, tier, question, as_user, tags, holdout, authoring_status, "
    "(golden_sql IS NOT NULL) AS has_sql, (golden_sandbox IS NOT NULL) AS has_sandbox, "
    "(golden_data IS NOT NULL) AS has_data, (golden_report IS NOT NULL) AS has_report, "
    "created_at, updated_at"
)
_FULL_COLS = (
    "id, source, dataset, tier, question, expectation, as_user, tags, holdout, "
    "authoring_status, golden_sql, golden_sandbox, golden_data, golden_report, "
    "created_at, updated_at"
)
_JSONB_COLS = {"tags", "golden_data", "golden_report"}


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
    expectation: str | None = None


def _jsonb_param(value: Any) -> str | None:
    return json.dumps(value) if value is not None else None


@router.get("/admin/eval-goldens")
async def list_goldens(
    dataset: str | None = None, admin: CurrentUser = Depends(require_admin)
) -> list[dict[str, Any]]:
    """List authored goldens, newest first; optionally scoped to one dataset."""
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


@router.get("/admin/eval-goldens/{golden_id}")
async def get_golden(golden_id: str, admin: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Full golden incl. all three stages, for the Builder / Evaluations tabs."""
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
                    " authoring_status, golden_sql, golden_sandbox, golden_data, golden_report) "
                    "VALUES ('authored', :q, :exp, :ds, :tier, :as_user, CAST(:tags AS jsonb), "
                    " :holdout, :status, :sql, :sandbox, "
                    " CAST(:data AS jsonb), CAST(:report AS jsonb)) "
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
    # Run the extract under this user id's RLS; defaults to the admin.
    as_user: str | None = None


@router.post("/admin/eval-goldens/prep")
async def prep(body: PrepIn, admin: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Draft a golden's data via the data-agent: run the confirmed SQL (Goal A) and,
    if a sandbox script is given, the metrics (Goal B) — the same governed path the
    agent uses. Returns extract rows + the produced report/skills for the builder.
    """
    return await prep_golden(
        sql=body.sql, code=body.code, user_id=body.as_user or admin.id, role="user"
    )


def _sandbox_code_from_steps(steps: list[Any]) -> str:
    """Recover the run_analysis scripts the agent ran, in order, from the trace.

    A model turn's tool_calls carry ``args`` as a JSON string; the run_analysis
    passes are what built the report, so joining their ``code`` gives a faithful
    starting point for the golden's sandbox stage that the curator then edits.
    """
    blocks: list[str] = []
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
            if isinstance(args, dict) and args.get("code"):
                blocks.append(str(args["code"]).strip())
    return "\n\n# --- next run_analysis pass ---\n\n".join(blocks)


class DraftIn(BaseModel):
    question: str
    as_user: str | None = None
    dataset: str = "nsw_sales"


@router.post("/admin/eval-goldens/draft")
async def draft(body: DraftIn, admin: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """First pass: run the data-agent on the question so the human can review and
    edit a pre-filled golden — the agent's SQL, sandbox script, extract rows, and
    the rendered report/pages. The curator then corrects each stage; a run of an
    upstream stage re-feeds the next. On a no-answer, `summary` explains why.
    """
    ans = await ask_agent(
        question=body.question,
        user_id=body.as_user or admin.id,
        role="user",
        plan="pro",
        dataset_slug=body.dataset or "nsw_sales",
    )
    report = ans.get("report") or {}
    queries = report.get("queries") or []
    sql = ans.get("sql") or (queries[0].get("sql") if queries else None)
    return {
        "sql": sql,
        "sandbox": _sandbox_code_from_steps(ans.get("steps") or []),
        "columns": ans.get("columns", []),
        "rows": ans.get("rows", []),
        "report": ans.get("report"),
        "pages": ans.get("pages"),
        "summary": report.get("summary"),
    }
