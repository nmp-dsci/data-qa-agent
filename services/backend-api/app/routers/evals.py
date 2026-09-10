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

import json
import re
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


# s50: how much of a sandbox pass's stdout the drill-down carries. Enough to
# read a printed frame head or a traceback; not the whole thing.
_STDOUT_CHARS = 2048
_SKILL_CALL = re.compile(r"\bskills\.([A-Za-z_][A-Za-z0-9_]*)\s*\(")


def _tool_call_args(trace: list[Any], suffix: str) -> list[dict[str, Any]]:
    """Every parsed ``args`` dict for tool calls named ``suffix`` (or
    ``mcp__dp__<suffix>`` on the Agent SDK path), in call order.

    A model turn's ``tool_calls`` carry ``args`` as a JSON string on both agent
    paths (see goldens.py ``_sandbox_code_from_steps``); this is the same walk,
    generalised so the extract SQL and the run_analysis code come out of it.
    """
    out: list[dict[str, Any]] = []
    for step in trace:
        if not isinstance(step, dict) or step.get("kind") != "model":
            continue
        for call in step.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            name = str(call.get("name") or "")
            if name != suffix and not name.endswith("__" + suffix):
                continue
            args = call.get("args")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    continue
            if isinstance(args, dict):
                out.append(args)
    return out


def _reduce_trace(trace: Any) -> dict[str, Any]:
    """The slice of ``query_runs.trace`` the case drill-down needs (s50).

    The full trace is every model turn's content and thinking — far too much to
    ship per case, and most of it is not what a reader investigating a wrong
    number wants. What they want is: which SQL ran (frame, rows, status,
    purpose) and what each ``run_analysis`` pass did (runtime, status, ms,
    stdout, error, the code itself, the skills it called).

    Two agent paths persist SQL differently. The pydantic-ai loop appended raw
    ``kind: sql`` steps; the Agent SDK path (s45) condenses them into the
    ``decision_log`` step and leaves frame/purpose only in the ``extract``
    tool-call args. Both are read here so older runs still drill down.
    """
    if not isinstance(trace, list):
        return {"sql": [], "analysis": None}

    extract_args = _tool_call_args(trace, "extract")
    by_sql = {str(a.get("sql") or "").strip(): a for a in extract_args if a.get("sql")}

    sql_steps: list[dict[str, Any]] = []
    for step in trace:
        if isinstance(step, dict) and step.get("kind") == "sql":
            sql_steps.append(
                {
                    "sql": step.get("sql"),
                    "status": step.get("status"),
                    "row_count": step.get("row_count"),
                    "frame": step.get("frame"),
                    "purpose": step.get("purpose") or step.get("why"),
                    "error": step.get("error"),
                }
            )
    if not sql_steps:
        for step in trace:
            if not isinstance(step, dict) or step.get("kind") != "decision_log":
                continue
            for d in step.get("decisions") or []:
                if not isinstance(d, dict) or d.get("type") != "sql":
                    continue
                args = by_sql.get(str(d.get("sql") or "").strip(), {})
                sql_steps.append(
                    {
                        "sql": d.get("sql"),
                        "status": d.get("status"),
                        "row_count": d.get("row_count"),
                        "frame": args.get("name"),
                        "purpose": args.get("purpose") or d.get("why"),
                        "error": d.get("error"),
                    }
                )

    rollup = next(
        (s for s in trace if isinstance(s, dict) and s.get("kind") == "analysis"),
        None,
    )
    if rollup is None:
        return {"sql": sql_steps, "analysis": None}

    codes = [str(a.get("code") or "") for a in _tool_call_args(trace, "run_analysis")]
    passes: list[dict[str, Any]] = []
    for i, p in enumerate(rollup.get("passes") or []):
        if not isinstance(p, dict):
            continue
        # The nth run_analysis call is the nth pass: the rollup appends one
        # entry per call, in order, so the code is recoverable by position.
        code = codes[i] if i < len(codes) else None
        stdout = p.get("stdout")
        passes.append(
            {
                "code_sha": p.get("code_sha"),
                "runtime": p.get("runtime"),
                "status": p.get("status"),
                "ms": p.get("ms"),
                "stdout": str(stdout)[:_STDOUT_CHARS] if stdout else None,
                "error": p.get("error"),
                "code": code or None,
                "skills_used": sorted(set(_SKILL_CALL.findall(code))) if code else None,
            }
        )
    return {
        "sql": sql_steps,
        "analysis": {
            "runtime": rollup.get("runtime"),
            "ms": rollup.get("ms"),
            "skills_used": rollup.get("skills_used") or [],
            "skill_gaps": rollup.get("skill_gaps") or [],
            "used_inline_math": rollup.get("used_inline_math"),
            "passes": passes,
        },
    }


def _num(value: Any) -> float | None:
    return None if value is None else float(value)


def _result_row(r: Any) -> dict[str, Any]:
    """One per-case result, shaped for the tab (columns from the JOIN in
    ``get_eval_run``). Every enrichment is nullable: older runs predate most of
    it, and a case whose agent run was lost has no ``query_runs`` row at all."""
    reduced = _reduce_trace(r.trace)
    return {
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
        # s50: the evidence — what the agent said, ran and spent, next to
        # what the golden expected, so a case is investigable in place.
        "otel_trace_id": r.otel_trace_id or r.qr_otel_trace_id,
        "mlflow_run_id": r.mlflow_run_id,
        "answer": r.answer,
        "sql_text": r.sql_text,
        "input_tokens": r.input_tokens,
        "output_tokens": r.output_tokens,
        "cache_read_tokens": r.cache_read_tokens,
        "cache_write_tokens": r.cache_write_tokens,
        "cost_usd": _num(r.cost_usd),
        "latency_ms": r.latency_ms,
        "degraded": r.degraded,
        "artifact_deck_url": r.artifact_deck_url,
        "artifact_sheet_url": r.artifact_sheet_url,
        "trace": reduced,
        "golden_answer": r.golden_answer,
        "label": r.label,
        "golden_sql": r.golden_sql,
        "golden_sandbox": r.golden_sandbox,
        "golden_checkpoints": r.golden_checkpoints or {},
        "grader": r.grader or {},
        "expectation": r.expectation,
    }


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
            _result_row(r)
            for r in await conn.execute(
                text(
                    "SELECT c.case_key, c.question, c.dataset, c.holdout, e.tier, e.passed, "
                    "e.notes, e.query_run_id, e.g1, e.g2, e.g3, e.g4, "
                    "e.judge, e.checkpoints, e.g5, e.otel_trace_id, e.mlflow_run_id, "
                    "qr.artifact_manifest, qr.sql_text, qr.input_tokens, qr.output_tokens, "
                    "qr.cache_read_tokens, qr.cache_write_tokens, qr.cost_usd, qr.latency_ms, "
                    "qr.otel_trace_id AS qr_otel_trace_id, qr.artifact_deck_url, "
                    "qr.artifact_sheet_url, qr.degraded, qr.trace, "
                    "m.content AS answer, "
                    "c.golden_answer, c.label, c.golden_sql, c.golden_sandbox, "
                    "c.checkpoints AS golden_checkpoints, c.grader, c.expectation "
                    "FROM app.eval_results e JOIN app.eval_cases c ON c.id = e.case_id "
                    "LEFT JOIN app.query_runs qr ON qr.id = e.query_run_id "
                    "LEFT JOIN app.messages m ON m.id = qr.message_id "
                    "WHERE e.eval_run_id = :id ORDER BY c.case_key"
                ),
                {"id": run_id},
            )
        ]

        # s50: spend for the run as a whole. Summed here from the same rows
        # rather than in the browser so the header and the table cannot drift.
        tokens = {
            "input": sum(r["input_tokens"] or 0 for r in results),
            "output": sum(r["output_tokens"] or 0 for r in results),
            "cache_read": sum(r["cache_read_tokens"] or 0 for r in results),
            "cache_write": sum(r["cache_write_tokens"] or 0 for r in results),
            "cost_usd": sum(r["cost_usd"] or 0.0 for r in results),
        }
        run["tokens"] = tokens
        if settings.mlflow_trace_experiment_id:
            run["mlflow_experiment_id"] = settings.mlflow_trace_experiment_id

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
