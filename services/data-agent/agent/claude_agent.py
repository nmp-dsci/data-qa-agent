"""Optional Claude path (Decision G).

Used only when ANTHROPIC_API_KEY is set and pydantic-ai is installed
(`uv sync --extra llm`). Any failure returns None so the caller falls back to
the deterministic offline stub — the app always answers.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from .config import settings
from .db import run_select
from .schema import SCHEMA_DOC

SYSTEM_PROMPT = f"""\
You are a data analyst. Answer the user's question about the housing dataset by
calling the run_sql tool with a single read-only SELECT, then summarising the
result in one or two sentences. Never invent numbers — only report what run_sql
returns. If run_sql returns zero rows, say the user has no access to that data.

{SCHEMA_DOC}
"""


@dataclass
class _Deps:
    user_id: str
    captured: dict[str, Any] = field(default_factory=dict)


async def maybe_answer_with_claude(question: str, *, user_id: str) -> dict[str, Any] | None:
    if not settings.anthropic_api_key:
        return None
    try:
        os.environ.setdefault("ANTHROPIC_API_KEY", settings.anthropic_api_key)
        from pydantic_ai import Agent, RunContext

        agent: Agent[_Deps, str] = Agent(
            f"anthropic:{settings.model}",
            deps_type=_Deps,
            system_prompt=SYSTEM_PROMPT,
        )

        @agent.tool
        async def run_sql(ctx: RunContext[_Deps], sql: str) -> str:
            result = await run_select(sql, user_id=ctx.deps.user_id)
            ctx.deps.captured = result
            return json.dumps(result)

        deps = _Deps(user_id=user_id)
        run = await agent.run(question, deps=deps)
        captured = deps.captured
        return {
            "answer": run.output,
            "sql": captured.get("sql"),
            "columns": captured.get("columns", []),
            "rows": captured.get("rows", []),
            "row_count": captured.get("row_count", 0),
            "engine": "claude",
        }
    except Exception as exc:  # noqa: BLE001 — never let the LLM path break the app
        print(f"[data-agent] Claude path unavailable, using stub: {exc}")
        return None
