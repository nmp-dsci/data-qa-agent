"""Skill-driven run_analysis codegen (s14 Golden Examples).

Given a question, the extract's columns, and a chosen set of skills, the model
(re)writes the run_analysis script that uses exactly those skills — plus one
short reason per skill (why it's applied). The sandbox preloads ``df``/``pd``/
``skills`` and blocks imports, so the code must never import. Falls back to a
deterministic scaffold when no LLM key is configured — the app never hard-fails.
"""

from __future__ import annotations

import inspect
import os
import re
from collections.abc import Sequence
from typing import Any

from .config import settings
from .provider import choose_provider

try:
    from pydantic import BaseModel
    from pydantic_ai import Agent

    class _SkillReason(BaseModel):
        skill: str = ""
        why: str = ""

    class _Scaffold(BaseModel):
        code: str = ""
        reasoning: list[_SkillReason] = []

    _PYDANTIC_AI_AVAILABLE = True
except ImportError:
    _PYDANTIC_AI_AVAILABLE = False

_ENV_VAR = {"deepseek": "DEEPSEEK_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
_FENCE = re.compile(r"^```(?:python|py)?\s*|\s*```$", re.IGNORECASE)
_IMPORT = re.compile(r"\s*(import |from \S+ import )")


def _clean_code(code: str) -> str:
    """Strip markdown fences and any import lines (the sandbox blocks imports)."""
    body = _FENCE.sub("", code.strip()).strip()
    return "\n".join(ln for ln in body.splitlines() if not _IMPORT.match(ln)).strip()


def _skill_details(names: Sequence[str]) -> str:
    from . import skills as skill_lib

    lines: list[str] = []
    for name in names:
        fn = getattr(skill_lib, name, None)
        if not callable(fn):
            continue
        doc = (inspect.getdoc(fn) or "").split("\n")[0]
        try:
            sig = str(inspect.signature(fn))
        except (TypeError, ValueError):
            sig = "()"
        lines.append(f"- skills.{name}{sig}: {doc}")
    return "\n".join(lines)


def _system_prompt() -> str:
    return (
        "You write a SHORT run_analysis script for a locked-down pandas sandbox.\n"
        "RULES:\n"
        "- `df` (the SQL extract as a DataFrame), `pd`, and `skills` are already in "
        "scope — NEVER import anything.\n"
        "- Prefer skills.<name> over hand-rolled maths; use ONLY the skills listed.\n"
        "- End by assigning the finished report: result = skills.build_report(...).\n"
        "- Return the code with no markdown fences, plus one short reason per skill.\n"
    )


def _instruction(question: str, columns: Sequence[str], skills: Sequence[str]) -> str:
    return (
        f"Question: {question}\n"
        f"df columns: {', '.join(columns) or '(unknown)'}\n\n"
        f"Use exactly these skills:\n{_skill_details(skills)}\n\n"
        "Write the run_analysis code over df using those skills, and give a reason per skill."
    )


def _stub(
    question: str, columns: Sequence[str], skills: Sequence[str], *, note: str | None = None
) -> dict[str, Any]:
    lines = ["# offline scaffold (no LLM key) — finish the args and wire into result"]
    for name in skills:
        lines.append(f"out = skills.{name}(df)  # edit args")
    lines.append("result = skills.build_report()")
    return {
        "code": "\n".join(lines),
        "reasoning": [{"skill": s, "why": "selected by the curator"} for s in skills],
        "engine": "stub",
        "error": note,
    }


async def scaffold_from_skills(
    *, question: str, columns: Sequence[str], skills: Sequence[str]
) -> dict[str, Any]:
    """Return {code, reasoning:[{skill,why}], engine, error} for the selected skills."""
    if not skills:
        return {"code": "", "reasoning": [], "engine": "stub", "error": "no skills selected"}
    if not _PYDANTIC_AI_AVAILABLE:
        return _stub(question, columns, skills)
    selected = choose_provider(
        settings.llm_provider, settings.deepseek_api_key, settings.anthropic_api_key
    )
    if selected is None:
        return _stub(question, columns, skills)
    provider, api_key = selected
    try:
        os.environ.setdefault(_ENV_VAR[provider], api_key)
        model_name = settings.deepseek_model if provider == "deepseek" else settings.model
        agent: Agent[None, _Scaffold] = Agent(
            f"{provider}:{model_name}",
            output_type=_Scaffold,
            system_prompt=_system_prompt(),
        )
        run = await agent.run(_instruction(question, columns, skills))
        out = run.output
        return {
            "code": _clean_code(out.code),
            "reasoning": [r.model_dump() for r in out.reasoning],
            "engine": provider,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 — never let the LLM path break the builder
        print(f"[data-agent] {provider} skill-codegen unavailable, using stub: {exc}")
        return _stub(question, columns, skills, note=str(exc))
