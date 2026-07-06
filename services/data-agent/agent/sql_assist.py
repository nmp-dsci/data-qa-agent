"""SQL editor AI assist (Phase C) — reuses the agent's SQL brain.

Four actions, all grounded in the same dbt-manifest schema the chat agent uses:

- generate: natural language -> a single read-only SELECT (dropped into the
  editor and auto-run client-side).
- explain:  describe what the selected SQL does, in plain English.
- fix:      repair SQL that errors or returns nothing.
- optimize: rewrite for efficiency, same result.

When an LLM provider key is configured (LLM_PROVIDER — see provider.py) the real
model authors/edits the SQL. Otherwise we fall back to the deterministic offline
planner for `generate` (build_sql), and an honest note for the transform actions
that genuinely need a model — the app never hard-fails on a missing key.
"""

from __future__ import annotations

import os
import re
from typing import Any

from .config import settings
from .nl2sql import build_sql
from .provider import choose_provider
from .schema import get_schema

# pydantic/pydantic-ai are only needed for the LLM path; guarding the import lets
# the deterministic stub (and its tests) run without the `llm` extra installed.
try:
    from pydantic import BaseModel
    from pydantic_ai import Agent

    class _Draft(BaseModel):
        """Structured output for the assist agent."""

        sql: str = ""
        explanation: str = ""

    _PYDANTIC_AI_AVAILABLE = True
except ImportError:
    _PYDANTIC_AI_AVAILABLE = False

_ENV_VAR = {"deepseek": "DEEPSEEK_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}

_STUB_TRANSFORM_NOTE = (
    "AI explain/fix/optimize needs an LLM provider configured "
    "(set DEEPSEEK_API_KEY or ANTHROPIC_API_KEY). Generating SQL from a question "
    "works offline via the built-in planner."
)

_FENCE = re.compile(r"^```(?:sql)?\s*|\s*```$", re.IGNORECASE)


def _clean_sql(sql: str) -> str:
    """Strip markdown code fences a model sometimes wraps SQL in."""
    return _FENCE.sub("", sql.strip()).strip()


def _instruction(action: str, prompt: str | None, sql: str | None) -> str:
    if action == "generate":
        return (
            "Write a single read-only SELECT that answers this question, and a "
            f"one-sentence explanation of what it returns.\n\nQuestion: {prompt}"
        )
    if action == "explain":
        return (
            "Explain in 2-3 plain-English sentences what this SQL returns. Put the "
            "explanation in `explanation` and return the SQL unchanged in `sql`.\n\n"
            f"SQL:\n{sql}"
        )
    if action == "fix":
        return (
            "This SQL errors or returns nothing useful. Return a corrected single "
            "read-only SELECT in `sql` and, in `explanation`, one sentence on what "
            f"you fixed.\n\nSQL:\n{sql}"
        )
    # optimize
    return (
        "Rewrite this SQL to run more efficiently while returning the same result. "
        "Return the optimized single read-only SELECT in `sql` and, in "
        f"`explanation`, one sentence on what you changed.\n\nSQL:\n{sql}"
    )


def _system_prompt() -> str:
    return f"""\
You help a user write and refine SQL in a governed SQL editor for a NSW
property-market app. Every query MUST be a single read-only SELECT (a CTE with
`WITH ... SELECT` is fine) — never INSERT/UPDATE/DELETE/DDL, never multiple
statements. Use fully schema-qualified table names (e.g.
marts.property_sales). The marts hold no precomputed growth%/yield% — compute
those from the additive sum/count columns. Keep results bounded with a sensible
LIMIT. Return valid Postgres SQL with no markdown fences.

{get_schema()}
"""


async def _assist_with_llm(
    action: str, prompt: str | None, sql: str | None
) -> dict[str, Any] | None:
    if not _PYDANTIC_AI_AVAILABLE:
        return None
    selected = choose_provider(
        settings.llm_provider, settings.deepseek_api_key, settings.anthropic_api_key
    )
    if selected is None:
        return None
    provider, api_key = selected
    try:
        os.environ.setdefault(_ENV_VAR[provider], api_key)
        model_name = settings.deepseek_model if provider == "deepseek" else settings.model
        agent: Agent[None, _Draft] = Agent(
            f"{provider}:{model_name}",
            output_type=_Draft,
            system_prompt=_system_prompt(),
        )
        run = await agent.run(_instruction(action, prompt, sql))
        draft = run.output
        cleaned = _clean_sql(draft.sql) if draft.sql else (sql or "")
        return {
            "sql": cleaned or None,
            "explanation": draft.explanation or None,
            "engine": provider,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 — never let the LLM path break the editor
        print(f"[data-agent] {provider} assist unavailable, using stub: {exc}")
        return None


def _assist_stub(action: str, prompt: str | None, sql: str | None) -> dict[str, Any]:
    if action == "generate":
        try:
            generated, _intent = build_sql(prompt or "")
        except Exception as exc:  # noqa: BLE001 — surface planner failure to the editor
            return {"sql": None, "explanation": None, "engine": "stub", "error": str(exc)}
        return {
            "sql": generated,
            "explanation": "Generated offline by the built-in query planner.",
            "engine": "stub",
            "error": None,
        }
    return {"sql": sql, "explanation": _STUB_TRANSFORM_NOTE, "engine": "stub", "error": None}


async def sql_assist(
    *, action: str, prompt: str | None, sql: str | None, user_id: str
) -> dict[str, Any]:
    """Return {sql, explanation, engine, error} for an editor AI-assist action."""
    llm = await _assist_with_llm(action, prompt, sql)
    if llm is not None:
        return llm
    return _assist_stub(action, prompt, sql)
