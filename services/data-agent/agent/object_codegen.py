"""Object-scoped run_analysis codegen (s14 Golden Examples, s16 full cascade).

Given a plain-English instruction to change ONE report object and the golden's
current stages (SQL extract, run_analysis, and the full list of objects), the
model rewrites the WHOLE run_analysis so it rebuilds every existing object plus
the requested change — and, only when the data isn't in the extract, a revised
SQL. It is a **self-correcting loop**, but orchestrated by us, not by the model's
tool-calling: a single structured generation, then WE run the governed extract +
sandbox and, on error, feed the failure back for one correction pass. That keeps
the LLM-call count bounded and predictable (≤ 2) — reliable where an open
tool-loop thrashes — while still verifying the pipeline before returning. The
endpoint (:func:`agent.main.agent_analysis_object`) then re-extracts + runs the
returned code authoritatively and lifts the target object back.

Falls back to a single call when there's no extract to validate against (unit
tests), and to a deterministic runnable stub when no LLM key is configured — so
the builder never hard-fails and never destroys the golden's other objects.

Mirrors :mod:`agent.skill_codegen`; reuses its ``_clean_code`` / ``_skill_details``
/ ``_ENV_VAR`` so every codegen path strips fences + imports identically.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from typing import Any

import pandas as pd

from .config import settings
from .provider import choose_provider
from .sandbox import explain_sandbox_error, run_code
from .sandbox.extract import extract
from .schema import get_schema_compact
from .skill_codegen import _ENV_VAR, _clean_code, _skill_details

try:
    from pydantic import BaseModel
    from pydantic_ai import Agent

    class _SkillReason(BaseModel):
        skill: str = ""
        why: str = ""

    class _ObjectScaffold(BaseModel):
        # Revised extract SQL, or "" to keep the current one (only change it when
        # the requested measure/dimension isn't already in the extract columns).
        sql: str = ""
        # run_analysis that rebuilds the WHOLE report (every object + the change).
        code: str = ""
        reasoning: list[_SkillReason] = []

    _PYDANTIC_AI_AVAILABLE = True
except ImportError:
    _PYDANTIC_AI_AVAILABLE = False


# Up to two correction passes after the initial generation — an edit costs ≤ 3
# model calls (bounded, no runaway). The corrections only fire when a run errored,
# so the common (first-try) success still costs a single fast call.
_EDIT_MAX_CORRECTIONS = 2

# The house skill each object type is built with, plus a one-line recipe for the
# model. Kept small on purpose — one obvious skill per visual object.
_OBJECT_RECIPE: dict[str, str] = {
    "compare": (
        "A line+bar combo — skills.dual_axis_chart(df, x_col=<x>, "
        "left_value_col=<bar measure>, right_value_col=<line measure>, "
        "series_col=<group column, or omit for none>, x_type='nominal' for a "
        "category/band x-axis else 'temporal'). Pass it as main_chart."
    ),
    "breakdown": (
        "A bar chart — skills.comparison_chart(df, category_col=<x>, "
        "value_col=<y>, series_col=<group column or omit>). Pass it as main_chart."
    ),
    "trend": (
        "A time series — s = skills.trend_series(df, ...); "
        "chart = skills.trend_chart(s, title=...). Pass chart as main_chart."
    ),
    "kpi": (
        "A headline number — read it with skills.latest_value(...), which returns "
        "{'value': float, 'month': 'YYYY-MM'} (a dict, NOT a number), so index it: "
        "lv = skills.latest_value(...); then pass headlines=[{'label':..., "
        "'value': lv['value'], 'basis': lv['month']}] (no main_chart)."
    ),
    "insight": ("An insight card — skills.make_insight(heading, body); pass insights=[...]."),
    "text": "A one-sentence summary passed as build_report(summary=...).",
}

# Skills the object-codegen may reference (a superset of every recipe above).
_ALLOWED_SKILLS: tuple[str, ...] = (
    "trend_series",
    "trend_chart",
    "comparison_chart",
    "dual_axis_chart",
    "distribution_chart",
    "profile_chart",
    "latest_value",
    "growth_rate",
    "top_growth",
    "driver_analysis",
    "make_insight",
    "build_report",
)


def _objects_digest(objects: Sequence[dict[str, Any]] | None) -> str:
    """One line per current object so the model knows what to preserve/rebuild.

    Each line names the object's type + role + the key data fields it renders
    (dimension/measure/x/y/label/heading) — enough to reproduce it, without the
    (possibly huge) row payload.
    """
    if not objects:
        return "(none yet — this is the golden's first object)"
    keys = (
        "dimension",
        "measure",
        "line_measure",
        "group",
        "x",
        "y",
        "series",
        "label",
        "value",
        "heading",
        "title",
        "intent",
    )
    lines: list[str] = []
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        raw = obj.get("data")
        data: dict[str, Any] = raw if isinstance(raw, dict) else {}
        fields = {k: data.get(k) for k in keys if data.get(k) not in (None, "", [])}
        tag = " target←EDIT THIS" if obj.get("_target") else ""
        lines.append(
            f"- {obj.get('type', '?')} (role={obj.get('role') or '—'}, "
            f"id={obj.get('element_id', '?')}){tag}: {json.dumps(fields, default=str)}"
        )
    return "\n".join(lines) or "(none)"


def _system_prompt() -> str:
    return (
        "You maintain the analysis behind a GOLDEN answer. You are given the current "
        "SQL extract + its columns, the current run_analysis script (which builds the "
        "whole report), the full list of report objects, and a curator's instruction to "
        "change ONE of them.\n"
        "Return a revised run_analysis that rebuilds the WHOLE report — EVERY existing "
        "object PLUS the requested change (never drop the others) — and a revised SQL "
        "when needed.\n"
        "WHEN TO CHANGE THE SQL: map every measure/dimension the instruction asks for to "
        "a REAL column in the listed extract columns. If ANY requested column is missing "
        "(e.g. the instruction wants `area_band` or a volume/`n_sold` the extract doesn't "
        "SELECT), you MUST return a revised `sql` that adds exactly those columns "
        "(keep the existing columns + WHERE so the other objects still build). Only when "
        "every needed column is already present do you leave `sql` empty.\n"
        "RULES:\n"
        "- `df` (the extract as a DataFrame), `pd`, and `skills` are already in scope — "
        "NEVER import anything. Aggregate with pandas first if the chart needs one row "
        "per x (e.g. a count for volume, a mean/median for price).\n"
        "- SOME SKILLS RETURN A DICT, NOT A NUMBER. skills.latest_value(...) always "
        "returns {'value': float, 'month': 'YYYY-MM'}; skills.growth_rate(...) and "
        "skills.top_growth(...) return {group: ...} as soon as group_col is passed. "
        "Index before you format or do arithmetic: use f\"{lv['value']:,.0f}\", never "
        'f"{lv:,.0f}" (that raises `unsupported format string passed to '
        "dict.__format__`). A skill can also return None when there is not enough "
        "history or the base is zero — guard before formatting.\n"
        "- Build charts with the house skills; end with "
        "result = skills.build_report(summary=..., headlines=[...], main_chart=<chart>, "
        "insights=[...]). Put the object the curator is editing as the primary chart of "
        "its kind; keep the others as headlines / insight charts so the report still "
        "reproduces them.\n"
        "- Return the run_analysis (no markdown fences), the revised sql (or empty to keep "
        "the current one), and one short reason per skill you used.\n"
    )


def _instruction(
    *,
    instruction: str,
    object_type: str,
    columns: Sequence[str],
    code: str,
    sql: str,
    objects: Sequence[dict[str, Any]] | None,
    schema: str = "",
    prior_code: str | None = None,
    error: str | None = None,
) -> str:
    recipe = _OBJECT_RECIPE.get(object_type, _OBJECT_RECIPE["breakdown"])
    schema_block = (
        f"Source-table schema — the ONLY columns you may add to the SQL come from "
        f"here (never invent a column name):\n{schema}\n\n"
        if schema.strip()
        else ""
    )
    base = (
        f"Target object type: {object_type}\n"
        f"Recipe for the target: {recipe}\n\n"
        f"Current extract SQL:\n{sql or '(none)'}\n\n"
        f"Current extract columns: {', '.join(columns) or '(unknown)'}\n\n"
        f"{schema_block}"
        f"Current run_analysis (rebuild ALL of this, changing only the target):\n"
        f"{code or '(none yet)'}\n\n"
        f"All current report objects:\n{_objects_digest(objects)}\n\n"
        f"Available skills:\n{_skill_details(_ALLOWED_SKILLS)}\n\n"
        f"Curator's instruction for the TARGET object:\n{instruction}\n"
    )
    if error:
        base += (
            f"\nYour previous attempt FAILED when run in the sandbox:\n{error}\n"
            f"Previous run_analysis:\n{prior_code or '(none)'}\n"
            "Fix the cause (a wrong column name usually means you must add it to the SQL) "
            "and return corrected sql + run_analysis.\n"
        )
    else:
        base += (
            "\nRewrite the run_analysis so it rebuilds the whole report with this change, "
            "add columns to the SQL if the requested data isn't in the extract, and return "
            "the code + sql + reasons.\n"
        )
    return base


def _stub(
    instruction: str, object_type: str, code: str, *, note: str | None = None
) -> dict[str, Any]:
    """No-LLM fallback: keep the existing code (never clobber) and record the ask.

    Without a model we can't safely rewrite the whole report, so we return the
    current run_analysis unchanged plus a note — the builder stays consistent and
    the curator finishes by hand. DeepSeek is configured in this env, so rare.
    """
    summary = instruction.strip().replace('"', "'")[:200]
    fallback = code.strip() or (
        "# offline scaffold (no LLM key) — wire the described chart into main_chart\n"
        f'result = skills.build_report(summary="{summary}")'
    )
    return {
        "code": fallback,
        "sql": None,
        "reasoning": [{"skill": "build_report", "why": f"offline stub for a {object_type}"}],
        "engine": "stub",
        "error": note or "no LLM key configured — run_analysis left unchanged",
    }


async def _call_model(
    *,
    instruction: str,
    object_type: str,
    columns: Sequence[str],
    code: str,
    sql: str,
    objects: Sequence[dict[str, Any]] | None,
    provider: str,
    model_name: str,
    schema: str = "",
    prior_code: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """One structured generation (or correction) call → {code, sql, reasoning}."""
    agent: Agent[None, _ObjectScaffold] = Agent(
        f"{provider}:{model_name}",
        output_type=_ObjectScaffold,
        system_prompt=_system_prompt(),
    )
    run = await agent.run(
        _instruction(
            instruction=instruction,
            object_type=object_type,
            columns=columns,
            code=code,
            sql=sql,
            objects=objects,
            schema=schema,
            prior_code=prior_code,
            error=error,
        )
    )
    out = run.output
    return {
        "code": _clean_code(out.code),
        "sql": (out.sql or "").strip(),
        "reasoning": [r.model_dump() for r in out.reasoning],
    }


def _report_ok(outcome: Any) -> bool:
    return (
        not outcome.error
        and isinstance(outcome.report, dict)
        and outcome.report.get("element_id") == "report"
    )


async def scaffold_object(
    *,
    instruction: str,
    object_type: str,
    columns: Sequence[str],
    code: str = "",
    sql: str = "",
    objects: Sequence[dict[str, Any]] | None = None,
    user_id: str | None = None,
    frame: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Return {code, sql, reasoning:[{skill,why}], engine, error} for the described edit.

    ``code`` rebuilds the WHOLE report (every object + the change); ``sql`` is a
    revised extract or None to keep the current one. With a ``user_id`` + ``frame``
    the generated code is verified in the governed sandbox and corrected once on
    error; without them the single generation is trusted (unit tests); with no LLM
    key the stub is returned (never clobbers the golden's other objects).
    """
    if not instruction.strip():
        return {
            "code": code,
            "sql": None,
            "reasoning": [],
            "engine": "stub",
            "error": "no instruction given",
        }
    if not _PYDANTIC_AI_AVAILABLE:
        return _stub(instruction, object_type, code)
    selected = choose_provider(
        settings.llm_provider, settings.deepseek_api_key, settings.anthropic_api_key
    )
    if selected is None:
        return _stub(instruction, object_type, code)
    provider, api_key = selected
    model_name = settings.deepseek_model if provider == "deepseek" else settings.model
    try:
        os.environ.setdefault(_ENV_VAR[provider], api_key)
        # The source-table schema grounds SQL rewrites in REAL column names, so the
        # model adds e.g. `area_band` instead of guessing a non-existent column.
        try:
            schema = get_schema_compact()
        except Exception:  # noqa: BLE001 — schema is best-effort grounding
            schema = ""
        gen = await _call_model(
            instruction=instruction,
            object_type=object_type,
            columns=columns,
            code=code,
            sql=sql,
            objects=objects,
            provider=provider,
            model_name=model_name,
            schema=schema,
        )

        def _result(error: str | None) -> dict[str, Any]:
            return {
                "code": gen["code"],
                "sql": gen["sql"] or None,
                "reasoning": gen["reasoning"],
                "engine": provider,
                "error": error,
            }

        # No extract to validate against (unit tests) — trust the single call.
        if user_id is None or frame is None:
            return _result(None)

        # Orchestrated verify → correct loop: WE run the extract + sandbox and, on
        # error, hand the failure back for one correction. Bounded LLM calls, no
        # tool-loop thrash.
        last_error: str | None = None
        for attempt in range(_EDIT_MAX_CORRECTIONS + 1):
            effective_sql = (gen["sql"] or "").strip() or sql
            verify_frame: pd.DataFrame | None = frame
            if effective_sql.strip() and effective_sql.strip() != sql.strip():
                try:
                    verify_frame, _ = await extract(effective_sql, user_id=user_id)
                except Exception as exc:  # noqa: BLE001 — feed the SQL error back to the model
                    verify_frame = None
                    last_error = f"revised extract failed: {exc}"
            if verify_frame is not None:
                outcome = run_code(gen["code"], df=verify_frame, frames={"extract": verify_frame})
                if _report_ok(outcome):
                    return _result(None)
                last_error = (
                    explain_sandbox_error(outcome.error)
                    or "run_analysis did not assign a report to `result`"
                )
            if attempt >= _EDIT_MAX_CORRECTIONS:
                break
            gen = await _call_model(
                instruction=instruction,
                object_type=object_type,
                columns=columns,
                code=code,
                sql=sql,
                objects=objects,
                provider=provider,
                model_name=model_name,
                schema=schema,
                prior_code=gen["code"],
                error=last_error,
            )
        # Exhausted without a clean run — return best-effort code + the error. The
        # endpoint's authoritative run then errors and the builder shows it (never
        # a partial/destructive write).
        return _result(last_error)
    except Exception as exc:  # noqa: BLE001 — never let the LLM path break the builder
        print(f"[data-agent] {provider} object-codegen unavailable, using stub: {exc}")
        return _stub(instruction, object_type, code, note=str(exc))
