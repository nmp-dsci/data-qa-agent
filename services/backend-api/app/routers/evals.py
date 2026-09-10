"""Read the eval history for the Evaluations tab (s24 M4).

The first read path these tables have ever had. Everything here is admin-only
and read-only: runs are written by ``scripts/eval_run.py``, never by the API, so
a score can never be produced by clicking something in the UI.

Three views, matching how the loop is actually used:

* **trend** — pass rate and pillar means across runs, so quality over time is
  visible rather than anecdotal.
* **compare** — one run against its baseline, with the regression verdict. This
  is the base-vs-experiment view.
* **cases** — per-case scores for one run, each linked to the ``query_runs``
  trace that produced it, so a failure is one click from its evidence.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text

from ..auth import CurrentUser, admin_or_demo_read
from ..config import settings
from ..db import rls_connection
from ..limits import check_demo_ip_rate

router = APIRouter()

# Enough history to see a trend without paging; the loop produces runs by hand,
# not continuously, so this is generous.
_RUN_LIMIT = 50


def _run_row(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "dataset": row.dataset,
        "pack": row.pack,
        "pack_version": row.pack_version,
        "experiment_id": row.experiment_id,
        "hypothesis": row.hypothesis,
        "base_run_id": str(row.base_run_id) if row.base_run_id else None,
        "judge_model": row.judge_model,
        "judge_prompt_hash": row.judge_prompt_hash,
        "totals": row.totals or {},
        "agent": {
            "fingerprint": row.fingerprint,
            "label": row.label,
            "provider": row.provider,
            "model_id": row.model_id,
            "prompt_hash": row.prompt_hash,
            "skills_hash": row.skills_hash,
            "knowledge_version": row.knowledge_version,
        },
    }


_RUN_SELECT = (
    "SELECT r.id, r.started_at, r.finished_at, r.dataset, r.pack, r.pack_version, "
    "r.experiment_id, r.hypothesis, r.base_run_id, r.judge_model, r.judge_prompt_hash, "
    "r.totals, v.fingerprint, v.label, v.provider, v.model_id, v.prompt_hash, "
    "v.skills_hash, v.knowledge_version "
    "FROM app.eval_runs r LEFT JOIN app.agent_versions v ON v.id = r.agent_version_id "
)


@router.get("/admin/eval-runs")
async def list_eval_runs(
    request: Request,
    limit: int = _RUN_LIMIT,
    admin: CurrentUser = Depends(admin_or_demo_read),
) -> list[dict[str, Any]]:
    """Every run, newest first — the trend view's data."""
    check_demo_ip_rate(request, "admin_read", settings.demo_rate_admin_read_per_min)
    async with rls_connection(admin.id) as conn:
        rows = await conn.execute(
            text(_RUN_SELECT + "ORDER BY r.started_at DESC LIMIT :limit"),
            {"limit": max(1, min(limit, 200))},
        )
        return [_run_row(r) for r in rows]


@router.get("/admin/eval-runs/{run_id}")
async def get_eval_run(
    request: Request,
    run_id: str,
    admin: CurrentUser = Depends(admin_or_demo_read),
) -> dict[str, Any]:
    """One run, its per-case results, and — when it is an experiment — the
    baseline it argues against, already diffed.

    The comparison is computed here rather than in the browser so the tab and
    ``scripts/eval_compare.py`` cannot disagree about what counts as a
    regression.
    """
    check_demo_ip_rate(request, "admin_read", settings.demo_rate_admin_read_per_min)
    try:
        UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid run_id") from exc

    async with rls_connection(admin.id) as conn:
        row = (await conn.execute(text(_RUN_SELECT + "WHERE r.id = :id"), {"id": run_id})).first()
        if row is None:
            return {"error": "no such run"}
        run = _run_row(row)

        results = [
            {
                "case_key": r.case_key,
                "question": r.question,
                "dataset": r.dataset,
                "tier": r.tier,
                "holdout": r.holdout,
                "passed": r.passed,
                "notes": r.notes,
                "query_run_id": str(r.query_run_id) if r.query_run_id else None,
                "g1": r.g1 or {},
                "g2": r.g2 or {},
                "g3": r.g3 or {},
                "g4": r.g4 or {},
                "g5": r.g5,
                # s49 M2: the judge's label + diagnosis, and the diagnostic
                # checkpoint scores. Both are read-only here and neither is part
                # of `passed` — the tab shows them beside the verdict precisely
                # so a reader can see what the gate did *not* consider.
                "judge": r.judge or {},
                "checkpoints": r.checkpoints or {},
                # s49 M0: the deck the run actually specified, so the tab (and
                # the presentation checkpoints) can be read against what was
                # asked for rather than only the URLs it produced.
                "artifact_manifest": r.artifact_manifest,
            }
            for r in await conn.execute(
                text(
                    "SELECT c.case_key, c.question, c.dataset, c.holdout, e.tier, e.passed, "
                    "e.notes, e.query_run_id, e.g1, e.g2, e.g3, e.g4, "
                    "e.judge, e.checkpoints, e.g5, "
                    "qr.artifact_manifest "
                    "FROM app.eval_results e JOIN app.eval_cases c ON c.id = e.case_id "
                    "LEFT JOIN app.query_runs qr ON qr.id = e.query_run_id "
                    "WHERE e.eval_run_id = :id ORDER BY c.case_key"
                ),
                {"id": run_id},
            )
        ]

        comparison: dict[str, Any] | None = None
        if run["base_run_id"]:
            base_rows = {
                r.case_key: r.passed
                for r in await conn.execute(
                    text(
                        "SELECT c.case_key, e.passed FROM app.eval_results e "
                        "JOIN app.eval_cases c ON c.id = e.case_id "
                        "WHERE e.eval_run_id = :id"
                    ),
                    {"id": run["base_run_id"]},
                )
            }
            base_row = (
                await conn.execute(
                    text(_RUN_SELECT + "WHERE r.id = :id"), {"id": run["base_run_id"]}
                )
            ).first()
            regressed = [
                r["case_key"] for r in results if base_rows.get(r["case_key"]) and not r["passed"]
            ]
            fixed = [
                r["case_key"]
                for r in results
                if r["case_key"] in base_rows and not base_rows[r["case_key"]] and r["passed"]
            ]
            base = _run_row(base_row) if base_row is not None else None
            # Runs graded against different packs are not measuring the same
            # thing; the tab says so rather than rendering a meaningless delta.
            comparable = base is not None and base["pack_version"] == run["pack_version"]
            comparison = {
                "base": base,
                "comparable": comparable,
                "regressed": regressed,
                "fixed": fixed,
                # The gate: a regression blocks regardless of the headline.
                "gate": "PASS" if not regressed else "FAIL",
            }

        return {"run": run, "results": results, "comparison": comparison}
