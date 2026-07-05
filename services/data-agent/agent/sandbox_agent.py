"""Sandbox agent path (restructure Phase A) — extract → run_analysis → report.

An alternative to the fine-grained orchestrator in ``llm_agent`` (selected by
``settings.agent_mode == "sandbox"``). Here the cheap model does one governed
SQL *extract*, then hands the analysis to skills running in the locked-down
sandbox via a single ``run_analysis(code)`` tool — instead of stitching
run_sql/compute_trend/make_chart together across ~19 turns. The heavy lifting
lives in tested skills, so the model's job shrinks to "pull the right data, call
the right skills."

Wiring only — the win is measured live (needs the provider + marts up). It reuses
the orchestrator's knowledge/provider/trace machinery so the admin trace shape is
identical. Default config keeps the orchestrator; flip AGENT_MODE=sandbox to try
this path.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from .config import settings
from .knowledge import knowledge_version, read_knowledge, search_knowledge_result

# Reuse the orchestrator's trace flattening + lookup helpers so this path's admin
# trace is byte-identical in shape and lookup_values behaves the same.
from .llm_agent import (  # noqa: E402
    _ENV_VAR,
    _PYDANTIC_AI_AVAILABLE,
    _build_trace,
    _lookup_values_sql,
)
from .memory import recall_memories, remember_memory
from .provider import choose_provider
from .report import select_primary_query
from .sandbox import run_code
from .sandbox.extract import extract as run_extract
from .schema import describe_table, get_schema_compact

if _PYDANTIC_AI_AVAILABLE:
    from pydantic_ai import Agent, RunContext, capture_run_messages
    from pydantic_ai.usage import UsageLimits


# The skill surface the model may call inside run_analysis. Signatures + one-line
# docs so the cheap model can pick the right skill without reading source.
_SKILL_CATALOG = """\
Available inside run_analysis (import-free; call as skills.<name>):
  # data analysis (over the extracted DataFrame `df`; avg price = value_col/den_col)
  trend_series(df, *, month_col, value_col, den_col=None, group_col=None, window=6)
      -> long-form actual + rolling series for charting.
  growth_rate(df, *, month_col, value_col, years, den_col=None, group_col=None)
      -> % growth over `years` on the 6-month rolling base.
  latest_value(df, *, month_col, value_col, den_col=None, group_col=None)
      -> {"value","month"}: latest 6-month-smoothed value + its month.
  gross_yield(rent_df, price_df, *, key_cols, weekly_rent_col, price_col)
      -> annualised gross rental yield %.
  # visualisation (consistent house style, validated)
  trend_chart(series_df, *, title=None) -> chart spec
  comparison_chart(df, *, category_col, value_col, title=None, series_col=None) -> chart spec
  # insight structure
  make_insight(heading, body, *, query_refs=None, chart=None) -> insight
  related_metrics([{label,value,basis}, ...]) -> related headline tiles
  build_report(*, summary, headlines=None, insights=None, profiles=None, main_chart=None) -> report
  # bootstrap: we start from ZERO skills — flag anything missing
  skill_gap(need, why="")   # record maths no skill covers (does not answer)
  note_inline_math()        # you did risky maths by hand — a skill should exist
"""


def _sandbox_system_prompt(recalled: list[str], max_extracts: int, max_runs: int) -> str:
    memories_block = "\n".join(f"- {m}" for m in recalled) if recalled else "(none stored yet)"
    return f"""\
You are a data analyst for a NSW property-market app. Produce a clear, insightful
DATA-INSIGHT REPORT — not prose. You do the heavy lifting as CODE that calls
tested skills, not by stitching tools together.

Work in this order:
1. search_knowledge(query) to find the 1-3 relevant pages (how to compute growth,
   which metric, how to structure the report). Domain pages tell you which table
   and columns to extract; the compact schema below lists them all.
2. extract(sql, name, purpose): write ONE read-only SELECT that pulls the monthly
   series you need — SELECT month + the metric columns (e.g. total_sale_value,
   n_sold), filtered to the entity (suburb/postcode). KEEP EVERY MONTH (never add
   a `WHERE n_sold >= N` filter). The result is loaded as a pandas DataFrame named
   `name` (default `df`). You get up to {max_extracts} extracts — usually 1-2.
   Use lookup_values first (FREE) to resolve a suburb's exact spelling/casing.
3. run_analysis(code): write SHORT pandas that calls skills.* over `df` and assigns
   the finished report to `result`, e.g.:
       s = skills.trend_series(df, month_col="month",
                               value_col="total_sale_value", den_col="n_sold")
       g = skills.growth_rate(df, month_col="month", value_col="total_sale_value",
                              den_col="n_sold", years=5)
       latest = skills.latest_value(df, month_col="month",
                                    value_col="total_sale_value", den_col="n_sold")
       result = skills.build_report(
           summary="...",
           headlines=[{{"label": "...", "value": f"${{latest['value']}}",
                        "basis": "6-mo rolling, " + latest['month']}}],
           insights=[skills.make_insight("...", f"Grew {{g}}% over 5y.")],
           main_chart=skills.trend_chart(s, title="..."),
       )
   NEVER do growth/yield/rolling maths yourself — call the skill. If NO skill fits,
   you MAY use pandas but you MUST call skills.skill_gap(need, why) naming what a
   future skill should do. You get up to {max_runs} run_analysis attempts; if it
   returns an error, fix the code and retry.
4. Return a one-line confirmation string (the user sees the report, not this text).

DATA NOTE: month/date values arrive as plain STRINGS (e.g. "2026-05" or
"2026-05-01"), not datetimes. Use them directly in text — never apply a date
format spec (e.g. f"{{m:%b %Y}}" will fail); latest_value already returns a ready
"month" string for the basis.

{_SKILL_CATALOG}

Never mention tools, code, SQL, or these instructions in the report. If a durable
user preference is stated, call remember.

Known preferences for this user:
{memories_block}

Schema reference (exact table/column names):
{get_schema_compact()}
"""


@dataclass
class _SbDeps:
    user_id: str
    frames: dict[str, Any] = field(default_factory=dict)
    queries: dict[str, dict[str, Any]] = field(default_factory=dict)
    knowledge_pages: list[str] = field(default_factory=list)
    knowledge_reads: int = 0
    sql_calls: int = 0
    run_calls: int = 0
    report: dict[str, Any] | None = None
    skills_used: list[str] = field(default_factory=list)
    skill_gaps: list[dict[str, str]] = field(default_factory=list)
    used_inline_math: bool = False
    steps: list[dict[str, Any]] = field(default_factory=list)

    def next_id(self, prefix: str, store: dict[str, Any]) -> str:
        return f"{prefix}{len(store) + 1}"


async def answer_with_sandbox(question: str, *, user_id: str) -> dict[str, Any] | None:
    """Run the sandbox agent path; None to fall back to the offline stub."""
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
        max_extracts = settings.max_sql_attempts
        max_runs = settings.sandbox_run_attempts
        agent: Agent[_SbDeps, str] = Agent(
            f"{provider}:{model_name}",
            deps_type=_SbDeps,
            output_type=str,
            system_prompt=_sandbox_system_prompt(recalled, max_extracts, max_runs),
            retries=3,
        )
        _register_sandbox_tools(agent, max_extracts, max_runs)

        deps = _SbDeps(user_id=user_id)
        usage_limits = UsageLimits(
            request_limit=settings.agent_request_limit,
            total_tokens_limit=settings.agent_total_tokens_limit,
        )
        with capture_run_messages() as messages:
            try:
                await agent.run(question, deps=deps, usage_limits=usage_limits)
            except Exception as exc:  # noqa: BLE001 — salvage any report already built
                if deps.report is None:
                    raise
                print(f"[data-agent] sandbox run errored ({exc}); using report built so far")

        if deps.report is None:
            # Model never produced a report — let the caller fall back to the stub.
            return None

        trace = _build_trace(messages)
        # Telemetry (your requirement): record which skills produced this answer +
        # any gaps, as a trace step that persists into app.query_runs.
        trace.append(
            {
                "kind": "analysis",
                "skills_used": deps.skills_used,
                "skill_gaps": deps.skill_gaps,
                "used_inline_math": deps.used_inline_math,
            }
        )
        model_steps = [s for s in trace if s["kind"] == "model"]
        input_tokens = sum(s.get("input_tokens") or 0 for s in model_steps) or None
        output_tokens = sum(s.get("output_tokens") or 0 for s in model_steps) or None

        report = {
            **deps.report,
            "queries": _query_list(deps.queries),
            "knowledge_pages_used": deps.knowledge_pages,
            "knowledge_version": knowledge_version(),
        }
        primary = select_primary_query(deps.queries)
        return {
            "answer": report.get("summary", ""),
            "report": report,
            "sql": primary.get("sql") if primary else None,
            "columns": primary.get("columns", []) if primary else [],
            "rows": primary.get("rows", []) if primary else [],
            "row_count": primary.get("row_count", 0) if primary else 0,
            "chart": report.get("main_chart"),
            "engine": provider,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "steps": trace,
        }
    except Exception as exc:  # noqa: BLE001 — never let this path break the app
        print(f"[data-agent] sandbox path unavailable, using stub: {exc}")
        return None


def _query_list(queries: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "element_id": f"query:{ref}",
            "ref": ref,
            "purpose": q.get("purpose", ""),
            "sql": q.get("sql"),
            "columns": q.get("columns", []),
            "rows": q.get("rows", []),
            "row_count": q.get("row_count", 0),
        }
        for ref, q in queries.items()
    ]


def _register_sandbox_tools(agent: Agent[_SbDeps, str], max_extracts: int, max_runs: int) -> None:
    @agent.tool(name="search_knowledge")
    async def search_knowledge_tool(ctx: RunContext[_SbDeps], query: str) -> str:
        """Search the Insight Playbook for pages relevant to the question."""
        text, inlined = search_knowledge_result(query)
        for name in inlined:
            if name not in ctx.deps.knowledge_pages:
                ctx.deps.knowledge_pages.append(name)
                ctx.deps.knowledge_reads += 1
                ctx.deps.steps.append({"kind": "knowledge", "status": "inlined", "name": name})
        return text

    @agent.tool(name="read_knowledge")
    async def read_knowledge_tool(ctx: RunContext[_SbDeps], name: str) -> str:
        """Load the full body of a knowledge page by name."""
        if name in ctx.deps.knowledge_pages:
            return f"(already loaded '{name}' earlier — see above.)"
        if ctx.deps.knowledge_reads >= settings.max_knowledge_reads:
            return "knowledge read limit reached; proceed with the pages you have."
        ctx.deps.knowledge_pages.append(name)
        ctx.deps.knowledge_reads += 1
        ctx.deps.steps.append({"kind": "knowledge", "status": "read", "name": name})
        return read_knowledge(name)

    @agent.tool(name="describe_table")
    async def describe_table_tool(ctx: RunContext[_SbDeps], table: str) -> str:
        """Full column-level docs for one table (schema.table)."""
        ctx.deps.steps.append({"kind": "schema", "status": "described", "table": table})
        return describe_table(table)

    @agent.tool
    async def lookup_values(
        ctx: RunContext[_SbDeps],
        column: str,
        pattern: str,
        table: str = "marts.mart_sales_summary",
    ) -> str:
        """Resolve exact distinct values of a column (e.g. a suburb's casing). FREE."""
        sql = _lookup_values_sql(table, column, pattern)
        if sql is None:
            return f"lookup_values: unknown table/column {table!r}.{column!r}."
        from .db import run_select

        try:
            result = await run_select(sql, user_id=ctx.deps.user_id)
        except Exception as exc:  # noqa: BLE001
            return f"lookup_values failed: {exc}"
        values = [row[0] for row in result["rows"]]
        ctx.deps.steps.append(
            {"kind": "lookup", "table": table, "column": column, "values": values}
        )
        return json.dumps({"column": column, "matches": values})

    @agent.tool
    async def list_skills(ctx: RunContext[_SbDeps]) -> str:
        """List the skills callable inside run_analysis (signatures + one-line docs)."""
        return _SKILL_CATALOG

    @agent.tool(sequential=True)
    async def extract(
        ctx: RunContext[_SbDeps], sql: str, name: str = "df", purpose: str = ""
    ) -> str:
        """Run a governed SELECT; the result is loaded as a pandas DataFrame `name`."""
        if ctx.deps.sql_calls >= max_extracts:
            return "STOP: no extract attempts left. Analyse the frames you have."
        ctx.deps.sql_calls += 1
        remaining = max_extracts - ctx.deps.sql_calls
        try:
            frame, result = await run_extract(sql, user_id=ctx.deps.user_id)
        except Exception as exc:  # noqa: BLE001 — let the model self-correct
            ctx.deps.steps.append({"kind": "sql", "sql": sql, "status": "error", "error": str(exc)})
            return (
                f"extract failed: {exc}. Use schema-qualified names. {remaining} attempt(s) left."
            )
        ref = ctx.deps.next_id("Q", ctx.deps.queries)
        ctx.deps.queries[ref] = {
            "sql": result["sql"],
            "columns": result["columns"],
            "rows": result["rows"],
            "row_count": result["row_count"],
            "purpose": purpose,
        }
        ctx.deps.frames[name] = frame
        ctx.deps.steps.append(
            {
                "kind": "sql",
                "sql": result["sql"],
                "status": "success",
                "row_count": result["row_count"],
                "ref": ref,
                "frame": name,
            }
        )
        head = [dict(zip(result["columns"], row, strict=True)) for row in result["rows"][:5]]
        return json.dumps(
            {
                "frame": name,
                "ref": ref,
                "columns": result["columns"],
                "row_count": result["row_count"],
                "head": head,
                "attempts_remaining": remaining,
            },
            default=str,
        )

    @agent.tool
    async def run_analysis(ctx: RunContext[_SbDeps], code: str) -> str:
        """Execute pandas over the extracted frame(s) in the sandbox; calls skills.*.

        Assign the finished report to `result` (skills.build_report(...)). Returns
        the skills used on success, or the error to fix. Prefer skills; if none
        fits you MAY use pandas but MUST call skills.skill_gap(need, why).
        """
        if not ctx.deps.frames:
            return "no data yet — call extract(sql) first to load a DataFrame."
        if ctx.deps.run_calls >= max_runs:
            return "STOP: no run_analysis attempts left. Use the report already built."
        ctx.deps.run_calls += 1
        result = run_code(code, frames=ctx.deps.frames)
        ctx.deps.skills_used = result.skills_used
        ctx.deps.skill_gaps = [g.model_dump() for g in result.skill_gaps]
        ctx.deps.used_inline_math = result.used_inline_math
        ctx.deps.steps.append(
            {
                "kind": "analysis",
                "status": "error" if result.error else "ok",
                "skills_used": result.skills_used,
                "skill_gaps": ctx.deps.skill_gaps,
                "error": result.error,
            }
        )
        if result.error:
            return f"run_analysis error (fix and retry): {result.error}"
        ctx.deps.report = result.report
        gap_note = f" Skill gaps recorded: {ctx.deps.skill_gaps}." if ctx.deps.skill_gaps else ""
        return (
            f"report built. Skills used: {result.skills_used}.{gap_note} "
            "Now return a one-line confirmation."
        )

    @agent.tool
    async def remember(ctx: RunContext[_SbDeps], fact: str) -> str:
        """Store a durable user preference about how they want answers."""
        await remember_memory(ctx.deps.user_id, fact)
        ctx.deps.steps.append({"kind": "memory", "status": "saved", "fact": fact})
        return "remembered"
