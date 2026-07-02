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


def _system_prompt(recalled: list[str], max_attempts: int) -> str:
    memories_block = "\n".join(f"- {m}" for m in recalled) if recalled else "(none stored yet)"
    return f"""\
You are a data analyst for a NSW property-market app. Write a single
read-only SELECT that directly lists the rows the question asks for and pass
it to run_sql — never a preliminary COUNT(*) or existence check.

The marts hold no precomputed growth%, rolling average, or yield% — you
compute those yourself from sum/count/median building blocks (see the schema
below for exact column names). suburb is a real dimension in the sales/yield
marts (postcode<->suburb is not 1:1, so include suburb in the grain when the
question is about a named suburb); rent has NO suburb (resolve a suburb to its
postcode via staging.int_postcode_geo first). Patterns to use:
- Growth over any window (e.g. "10-year growth"): compare
  total_sale_value/n_sold (or median_price) between two months/years with a
  self-join or two CTEs, not a stored growth column. To combine per-suburb
  sales growth with (postcode-level) rent growth in one query, build each side
  as its own CTE and join on postcode — for example:
  ```
  WITH s_bounds AS (
    SELECT postcode, suburb, min(month) fm, max(month) lm
    FROM marts.mart_sales_summary WHERE property_type = 'ALL'
    GROUP BY postcode, suburb
  ), s_growth AS (
    SELECT b.postcode, b.suburb,
      (sl.total_sale_value/nullif(sl.n_sold,0) - sf.total_sale_value/nullif(sf.n_sold,0))
      / nullif(sf.total_sale_value/nullif(sf.n_sold,0), 0) * 100 AS sales_growth_pct
    FROM s_bounds b
    JOIN marts.mart_sales_summary sf
      ON sf.postcode=b.postcode AND sf.suburb=b.suburb
      AND sf.property_type='ALL' AND sf.month=b.fm
    JOIN marts.mart_sales_summary sl
      ON sl.postcode=b.postcode AND sl.suburb=b.suburb
      AND sl.property_type='ALL' AND sl.month=b.lm
  ), r_bounds AS (
    SELECT postcode, min(month) fm, max(month) lm
    FROM marts.mart_rent_summary WHERE property_type = 'ALL' GROUP BY postcode
  ), r_growth AS (
    SELECT b.postcode,
      (rl.total_weekly_rent/nullif(rl.n_rented,0) - rf.total_weekly_rent/nullif(rf.n_rented,0))
      / nullif(rf.total_weekly_rent/nullif(rf.n_rented,0), 0) * 100 AS rent_growth_pct
    FROM r_bounds b
    JOIN marts.mart_rent_summary rf
      ON rf.postcode=b.postcode AND rf.property_type='ALL' AND rf.month=b.fm
    JOIN marts.mart_rent_summary rl
      ON rl.postcode=b.postcode AND rl.property_type='ALL' AND rl.month=b.lm
  )
  SELECT s.suburb, s.postcode, s.sales_growth_pct, r.rent_growth_pct
  FROM s_growth s JOIN r_growth r ON r.postcode = s.postcode
  ORDER BY s.sales_growth_pct DESC LIMIT 10
  ```
  Adapt this shape (grain, filters, window) to what the question actually
  asks — don't reuse it verbatim if it doesn't fit. For a postcode-level (not
  per-suburb) figure, SUM total_sale_value and n_sold across the postcode's
  suburbs instead of grouping by suburb.
- Rolling average (e.g. "12-month rolling average"): a window function over
  the summary mart, e.g. `avg(total_sale_value / nullif(n_sold, 0)) over
  (order by month rows between 11 preceding and current row)`.
- Yield: (median_rent * 52 / median_price) * 100, or the volume-weighted
  version from total_weekly_rent/n_rented and total_sale_value/n_sold.
- "Current"/"latest" figures (e.g. current yield): monthly buckets are
  small and the single most-recent month is often too thin to trust or
  even present. Don't filter to `month = max(month)` and stop — pick the
  most recent month that actually has adequate coverage, e.g. order by
  month descending and take the first row per postcode, or filter to
  n_sold/n_rented above a small floor before picking "latest".
- A time series comparing sale price and rent together (e.g. for a chart):
  UNION ALL the two summary marts into one result with a `series` label
  column (e.g. 'avg_sale_price', 'avg_rent') and a shared `month` column, so
  make_chart can encode `color` by `series` — don't run two separate
  run_sql calls for this, one UNION ALL query gets both series at once.
Name what you compute consistently: alias a growth calculation as
`sales_growth_pct`/`rent_growth_pct`, and a yield calculation as
`gross_yield_pct` — same names the old precomputed columns used, so charts
and downstream consumers can rely on them regardless of the exact SQL you
wrote to get there.

You get up to {max_attempts} run_sql attempts for this question. If a query
fails (syntax error, unknown column), fix it and try again. If a query
succeeds but returns zero rows or a result that looks wrong for the question
asked (e.g. a suspicious value, a join that silently dropped everything),
and you still have attempts left, check your filters/join keys (postcode,
property_type, month — see the schema below for exact spellings) and try a
corrected query rather than accepting a bad result. Only tell the user the
data isn't available once you've used your attempts or you're confident the
data genuinely doesn't exist.

IMPORTANT: only the result of your LAST run_sql call is shown to the user
(as the data table/chart backing your answer) — not any earlier call. Once a
query has actually given you the data your answer needs, stop calling
run_sql; never make a further "let me double-check" or exploratory call
after that point, even if you have attempts left, because it will silently
replace the real data with whatever that later query returned and the user
will see data that doesn't match your answer. Then summarise the result in
one or two sentences. Never invent numbers — only report what your last
run_sql call actually returned.

Speak only about the property data and the user's question. Never mention
run_sql, tool calls, attempts, remaining/used attempts, retries, or any other
implementation detail in your answer — the user only sees your final
summary, never your reasoning. This applies even when things went wrong: if
you're unsure what your last query returned, or you ran out of attempts
without a clean result, do not think out loud about it ("let me check...",
"actually...", "hmm") — write one short, calm sentence stating you weren't
able to retrieve reliable data for this question and, if useful, suggest the
user rephrase or narrow it. A confused-sounding answer is worse than a short
honest one.

If a chart would help illustrate the answer (e.g. ranking suburbs, a trend
over time), call make_chart with `mark` (e.g. "bar", "line", "point") and
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
        max_attempts = settings.max_sql_attempts
        agent: Agent[_Deps, str] = Agent(
            f"{provider}:{model_name}",
            deps_type=_Deps,
            system_prompt=_system_prompt(recalled, max_attempts),
        )

        @agent.tool
        async def run_sql(ctx: RunContext[_Deps], sql: str) -> str:
            # Counts every attempt — failure or success — not just successes:
            # a query that runs fine but looks wrong for the question is exactly
            # the case the model should be able to retry, not just syntax errors.
            if ctx.deps.sql_calls >= max_attempts:
                return (
                    "STOP: you've used all your run_sql attempts for this question. "
                    "Write your final answer now from the last result returned above "
                    "(or say you couldn't get reliable data) — do not mention this note."
                )
            ctx.deps.sql_calls += 1
            remaining = max_attempts - ctx.deps.sql_calls
            try:
                result = await run_select(sql, user_id=ctx.deps.user_id)
            except Exception as exc:  # noqa: BLE001 — let the model see and self-correct
                return (
                    f"query failed: {exc}. Use fully schema-qualified table names "
                    f"(e.g. marts.mart_sales_summary). {remaining} attempt(s) remaining."
                )
            ctx.deps.captured = result
            note = "" if remaining else " (no attempts remaining — this must be your final answer)"
            return json.dumps(result) + f"\n[{remaining} attempt(s) remaining{note}]"

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
