"""Object-scoped run_analysis codegen (s14 Golden Examples).

Given a plain-English instruction for ONE report object (its type + what data it
should show) and the extract's columns, the model writes a short run_analysis
script that builds exactly that chart as the report's ``main_chart`` using the
house chart skills, and assigns the finished report to ``result``. The Golden
builder then runs it in the sandbox and lifts the object's data back into the
presentation (see ``/agent/analysis/object``). Falls back to a deterministic,
runnable stub when no LLM key is configured — the builder never hard-fails.

Mirrors :mod:`agent.skill_codegen`; reuses its ``_clean_code`` / ``_skill_details``
/ ``_ENV_VAR`` so both codegen paths strip fences + imports identically.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

from .config import settings
from .provider import choose_provider
from .skill_codegen import _ENV_VAR, _clean_code, _skill_details

try:
    from pydantic import BaseModel
    from pydantic_ai import Agent

    class _SkillReason(BaseModel):
        skill: str = ""
        why: str = ""

    class _ObjectScaffold(BaseModel):
        code: str = ""
        reasoning: list[_SkillReason] = []

    _PYDANTIC_AI_AVAILABLE = True
except ImportError:
    _PYDANTIC_AI_AVAILABLE = False


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
        "A headline number — read it with skills.latest_value(...) and pass "
        "headlines=[{'label':..., 'value':..., 'basis':...}] (no main_chart)."
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


def _system_prompt() -> str:
    return (
        "You write a SHORT run_analysis script for a locked-down pandas sandbox that "
        "builds ONE chart a curator described, then assigns the finished report to "
        "`result`.\n"
        "RULES:\n"
        "- `df` (the SQL extract as a DataFrame), `pd`, and `skills` are already in scope "
        "— NEVER import anything.\n"
        "- Map the described measures/dimension to the REAL df columns listed below; never "
        "invent a column that isn't there. Aggregate with pandas first if the chart needs "
        "one row per x (e.g. a count for volume or a median for price).\n"
        "- Build the chart with the house skill named in the recipe for this object type, "
        "then end with `result = skills.build_report(summary=..., main_chart=<chart>)` "
        "(for a kpi pass headlines=[...] and no main_chart).\n"
        "- Return the code with no markdown fences, plus one short reason per skill used.\n"
    )


def _instruction(*, instruction: str, object_type: str, columns: Sequence[str], code: str) -> str:
    recipe = _OBJECT_RECIPE.get(object_type, _OBJECT_RECIPE["breakdown"])
    ctx = (
        f"\nCurrent run_analysis (context — you may replace it entirely):\n{code}\n"
        if code.strip()
        else ""
    )
    return (
        f"Object type: {object_type}\n"
        f"Recipe: {recipe}\n"
        f"df columns: {', '.join(columns) or '(unknown)'}\n\n"
        f"Available skills:\n{_skill_details(_ALLOWED_SKILLS)}\n{ctx}\n"
        f"Curator's request for THIS object:\n{instruction}\n\n"
        "Write the run_analysis code that builds exactly this chart as the report's "
        "main_chart, and give one reason per skill you use."
    )


def _stub(instruction: str, object_type: str, *, note: str | None = None) -> dict[str, Any]:
    """No-LLM fallback: a runnable script that records the request as the summary.

    It can't map the instruction onto columns without a model, so it produces a
    valid (chart-less) report the builder can still save — the curator finishes
    the chart by hand. DeepSeek is configured in this env, so this is rarely hit.
    """
    summary = instruction.strip().replace('"', "'")[:200]
    code = (
        "# offline scaffold (no LLM key) — wire the described chart into main_chart\n"
        f'result = skills.build_report(summary="{summary}")'
    )
    return {
        "code": code,
        "reasoning": [{"skill": "build_report", "why": f"offline stub for a {object_type}"}],
        "engine": "stub",
        "error": note,
    }


async def scaffold_object(
    *, instruction: str, object_type: str, columns: Sequence[str], code: str = ""
) -> dict[str, Any]:
    """Return {code, reasoning:[{skill,why}], engine, error} for the described object."""
    if not instruction.strip():
        return {"code": code, "reasoning": [], "engine": "stub", "error": "no instruction given"}
    if not _PYDANTIC_AI_AVAILABLE:
        return _stub(instruction, object_type)
    selected = choose_provider(
        settings.llm_provider, settings.deepseek_api_key, settings.anthropic_api_key
    )
    if selected is None:
        return _stub(instruction, object_type)
    provider, api_key = selected
    try:
        os.environ.setdefault(_ENV_VAR[provider], api_key)
        model_name = settings.deepseek_model if provider == "deepseek" else settings.model
        agent: Agent[None, _ObjectScaffold] = Agent(
            f"{provider}:{model_name}",
            output_type=_ObjectScaffold,
            system_prompt=_system_prompt(),
        )
        run = await agent.run(
            _instruction(
                instruction=instruction, object_type=object_type, columns=columns, code=code
            )
        )
        out = run.output
        return {
            "code": _clean_code(out.code),
            "reasoning": [r.model_dump() for r in out.reasoning],
            "engine": provider,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 — never let the LLM path break the builder
        print(f"[data-agent] {provider} object-codegen unavailable, using stub: {exc}")
        return _stub(instruction, object_type, note=str(exc))
