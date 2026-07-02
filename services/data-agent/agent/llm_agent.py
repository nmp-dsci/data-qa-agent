"""Pluggable LLM path (Decision G): DeepSeek by default, Claude via config.

Used only when the configured provider's API key is set and pydantic-ai is
installed (`uv sync --extra llm`). Any failure returns None so the caller
falls back to the deterministic offline stub — the app always answers.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

import logfire

from .chart import UnsafeChartSpecError, validate_chart_spec
from .config import settings
from .db import run_select
from .memory import recall_memories, remember_memory
from .provider import choose_provider
from .schema import get_schema

try:
    # Imported at module level (not inside the function) so that pydantic-ai's
    # @agent.tool decorator can resolve the `RunContext` string annotation
    # (from `from __future__ import annotations`) against this module's
    # globals — a function-local import would leave it undefined there and
    # raise NameError at decoration time. Guarded so the module (and the app)
    # still loads when the `llm` extra isn't installed; the stub is used then.
    from pydantic_ai import Agent, RunContext

    logfire.instrument_pydantic_ai()
    logfire.instrument_httpx(capture_all=True)
    _PYDANTIC_AI_AVAILABLE = True
except ImportError:
    _PYDANTIC_AI_AVAILABLE = False

_ENV_VAR = {"deepseek": "DEEPSEEK_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


def _system_prompt(recalled: list[str]) -> str:
    memories_block = "\n".join(f"- {m}" for m in recalled) if recalled else "(none stored yet)"
    return f"""\
You are a data analyst for a NSW property-market app. Write a single
read-only SELECT that directly lists the rows the question asks for (e.g. a
ranked list of suburbs) and pass it to run_sql — never a preliminary COUNT(*)
or existence check. If the question involves BOTH sale price and rent, JOIN
mart_sales_growth and mart_rent_growth on (postcode, property_type) in that
one query and select sales_growth_pct and rent_growth_pct by their original
column names (don't rename or alias them). Then summarise the result in one
or two sentences. Never invent numbers — only report what run_sql returns.
If run_sql returns zero rows, tell the user the suburb/area/data they asked
about isn't in the currently available dataset — don't speculate about why.

Speak only about the property data and the user's question. Never mention
run_sql, tool calls, queries, retries, or any other implementation detail in
your answer — the user only sees your final summary, not your reasoning.

If a chart would help illustrate the answer (e.g. ranking suburbs, a trend
over years), call make_chart with `mark` (e.g. "bar", "line", "point") and
`encoding` (a Vega-Lite encoding object mapping channels like x/y/color to
run_sql's column names) — the underlying data is filled in automatically
from the run_sql result, you never supply it yourself.

If the user states an explicit, durable preference about how they want
answers (e.g. "I only care about units, not houses", "always show yield not
growth"), call remember with that fact. Don't call remember for the answer
content itself or for one-off questions.

Known preferences for this user, recalled from past conversations:
{memories_block}

{get_schema()}
"""


@dataclass
class _Deps:
    user_id: str
    captured: dict[str, Any] = field(default_factory=dict)
    chart: dict[str, Any] | None = None
    sql_calls: int = 0


async def maybe_answer_with_llm(question: str, *, user_id: str) -> dict[str, Any] | None:
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

        recalled = await recall_memories(user_id, question)
        model_name = settings.deepseek_model if provider == "deepseek" else settings.model
        agent: Agent[_Deps, str] = Agent(
            f"{provider}:{model_name}",
            deps_type=_Deps,
            system_prompt=_system_prompt(recalled),
        )

        @agent.tool
        async def run_sql(ctx: RunContext[_Deps], sql: str) -> str:
            if ctx.deps.sql_calls >= 1:
                return (
                    "STOP: do not call run_sql again. Write your final answer now, using "
                    "only the JSON already returned above — do not mention this note."
                )
            try:
                result = await run_select(sql, user_id=ctx.deps.user_id)
            except Exception as exc:  # noqa: BLE001 — let the model see and self-correct
                return (
                    f"query failed: {exc}. Use fully schema-qualified table names "
                    "(e.g. marts.mart_sales_growth)."
                )
            ctx.deps.sql_calls += 1
            ctx.deps.captured = result
            return json.dumps(result)

        @agent.tool
        async def make_chart(
            ctx: RunContext[_Deps],
            mark: str,
            encoding: dict[str, Any],
            title: str | None = None,
        ) -> str:
            spec: dict[str, Any] = {"mark": mark, "encoding": encoding}
            if title:
                spec["title"] = title
            try:
                validated = validate_chart_spec(spec)
            except UnsafeChartSpecError as exc:
                return f"chart rejected: {exc}"
            columns = ctx.deps.captured.get("columns", [])
            rows = ctx.deps.captured.get("rows", [])
            values = [dict(zip(columns, row, strict=True)) for row in rows[:500]]
            ctx.deps.chart = {**validated, "data": {"values": values}}
            return "chart captured"

        @agent.tool
        async def remember(ctx: RunContext[_Deps], fact: str) -> str:
            await remember_memory(ctx.deps.user_id, fact)
            return "remembered"

        deps = _Deps(user_id=user_id)
        run = await agent.run(question, deps=deps)
        captured = deps.captured
        usage = run.usage
        return {
            "answer": run.output,
            "sql": captured.get("sql"),
            "columns": captured.get("columns", []),
            "rows": captured.get("rows", []),
            "row_count": captured.get("row_count", 0),
            "chart": deps.chart,
            "engine": provider,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
        }
    except Exception as exc:  # noqa: BLE001 — never let the LLM path break the app
        print(f"[data-agent] {provider} path unavailable, using stub: {exc}")
        return None
