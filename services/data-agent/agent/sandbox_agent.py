"""The agent path — extract → run_analysis → report (restructure).

The only agent architecture (the fine-grained-tool orchestrator was removed once
this proved out). The cheap model does one governed SQL *extract*, then hands the
analysis to tested skills running in the locked-down sandbox via a single
``run_analysis(code)`` tool — instead of stitching run_sql/compute_trend/make_chart
together across ~19 turns. The heavy lifting lives in the skills, so the model's
job shrinks to "pull the right data, call the right skills."

Analysis/presentation know-how lives in the skills (their docstrings + the catalog
below), so knowledge is consulted only for DOMAIN/extract guidance — which table,
columns, and grain to pull. Trace flattening / lookup helpers come from
``agent_common``; when no provider key is set the caller falls back to the
deterministic offline stub.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any

from .agent_common import (
    _ENV_VAR,
    _PYDANTIC_AI_AVAILABLE,
    _build_trace,
    _lookup_values_sql,
)
from .config import settings
from .knowledge import knowledge_version, read_knowledge, search_knowledge_result
from .memory import recall_memories, remember_memory
from .pages import (
    compose_insights_page,
    compose_pages,
    compose_summary_page,
    page_plan,
    planned_kinds,
)
from .provider import choose_provider
from .report import select_primary_query
from .sandbox import run_code
from .sandbox.extract import extract as run_extract
from .schema import describe_table, list_marts

if _PYDANTIC_AI_AVAILABLE:
    from pydantic_ai import Agent, RunContext, capture_run_messages
    from pydantic_ai.usage import UsageLimits


# The skill surface the model may call inside run_analysis. Signatures + one-line
# docs so the cheap model can pick the right skill without reading source.
_SKILL_CATALOG = """\
Available inside run_analysis (import-free; call as skills.<name>):
  # data analysis (over the extracted DataFrame `df`; a rate = value_col/den_col,
  #   e.g. an additive total over its count)
  trend_series(df, *, month_col, value_col, den_col=None, group_col=None, window=6)
      -> long-form actual + rolling series for charting.
  rolling_average(df, *, month_col, value_col, den_col=None, group_col=None, window=6)
      -> [month, value, series] just the N-month smoothed line (no actual layer).
  growth_rate(df, *, month_col, value_col, years, den_col=None, group_col=None)
      -> % growth over `years` on the 6-month rolling base. If the series nearly
         covers `years` (>=80%) it clamps to the full available span; if far
         short it returns None — guard None before formatting (f"{g:.1f}" on
         None raises). Never probe min/max month first just to pick `years`.
  top_growth(df, *, month_col, value_col, group_col, years, den_col=None, n=5, ascending=False)
      -> DataFrame [group, growth_pct] ranked: the "top-growth groups" ranker.
  latest_value(df, *, month_col, value_col, den_col=None, group_col=None)
      -> {"value","month"}: latest 6-month-smoothed value + its month.
  gross_yield(rent_df, price_df, *, key_cols, weekly_rent_col, price_col)
      -> annualised gross rental yield %.
  driver_analysis(df, *, dimensions, value_col, den_col=None, top=3)
      -> which attribute most explains high/low values of the metric (% contribution):
         {"top_dimension", "overall", "ranked":[{dimension, score_pct, levels}]}.
         Use for "why/what drives X" and to power the Insights breakdown.
  # visualisation (consistent house style, validated)
  trend_chart(series_df, *, title=None) -> chart spec
  comparison_chart(df, *, category_col, value_col, title=None, series_col=None) -> chart spec
  dual_axis_chart(df, *, x_col, left_value_col, right_value_col, title=None,
                  left_title=None, right_title=None, x_type="temporal") -> chart spec
      -> bars + secondary-axis line for two metrics with different scales.
  distribution_chart(df, *, value_col, title=None, category_col=None) -> chart spec
      -> histogram for spread/outlier/distribution questions.
  profile_chart(df, *, category_col, segment_col, value_col, title=None, normalize=True)
      -> stacked composition bars (each entity's segment mix as % shares).
  # insight structure
  make_insight(heading, body, *, query_refs=None, chart=None) -> insight
  related_metrics([{label,value,basis}, ...]) -> related headline tiles
  build_report(*, summary, headlines=None, insights=None, profiles=None, main_chart=None) -> report
  build_insights(*, insights, profiles=None) -> pass-2 patch that merges insight
      cards into the report already built by build_report (never replaces it)
  # bootstrap: we start from ZERO skills — flag anything missing
  skill_gap(need, why="")   # record maths no skill covers (does not answer)
  note_inline_math()        # you did risky maths by hand — a skill should exist
"""


def _sandbox_system_prompt(
    recalled: list[str], max_extracts: int, max_runs: int, include_insights: bool = True
) -> str:
    memories_block = "\n".join(f"- {m}" for m in recalled) if recalled else "(none stored yet)"
    if include_insights:
        pass_plan = f"""\
   The app STREAMS each page to the user the moment you finish it, so work in
   TWO SHORT PASSES over the frame(s) you already extracted (never re-extract):
   PASS 1 — the summary page (always FIRST, keep it FAST — no attribute
   slicing here): aggregate to headline level and assign
       result = skills.build_report(
           summary="...",
           headlines=[{{"label": "...", "value": ..., "basis": ...}}],
           main_chart=skills.trend_chart(s, title="..."),
       )
       Always include a latest_value + growth headline and the trend/comparison
       chart — this page captures the answer and renders IMMEDIATELY.
   PASS 2 — the insights page (a SECOND run_analysis call, right after pass 1
   succeeds): slice the SAME frame by its attribute columns (e.g. a type or
   band; call driver_analysis when the question asks WHY or attributes exist)
   and assign
       result = skills.build_insights(insights=[
           skills.make_insight("...", "...", chart=skills.comparison_chart(...)),
       ])
       naming the strongest driver of the Page-1 numbers with a
       comparison_chart of its levels. It merges into the pass-1 report.
   You get up to {max_runs} run_analysis attempts TOTAL across both passes; if
   a pass returns an error, fix the code and retry."""
    else:
        pass_plan = f"""\
   Write SHORT pandas that aggregates to headline level and assigns the report:
       result = skills.build_report(
           summary="...",
           headlines=[{{"label": "...", "value": ..., "basis": ...}}],
           main_chart=skills.trend_chart(s, title="..."),
       )
       Always include a latest_value + growth headline and the trend/comparison
       chart — one summary page IS the answer; do not add insights.
   You get up to {max_runs} run_analysis attempts; if it returns an error, fix
   the code and retry."""
    return f"""\
You are a data-insight agent. You answer questions over whatever datasets the
marts schema exposes — do NOT assume a particular domain; discover what the data
is from the mart index and the knowledge base. Produce a clear, insightful
DATA-INSIGHT REPORT — not prose. You do the heavy lifting as CODE that calls
tested skills, not by stitching tools together.

Work in this order:
1. search_knowledge(query, why="..."): find the 1-2 relevant knowledge pages for this
   dataset/metric — the grain to pull, which columns mean what, join keys, and any
   gotchas. This is where dataset-specific rules live; always start here. You do
   NOT need knowledge for how to compute growth/yield or structure the report —
   that lives in the tested skills below; just call them.
2. describe_table('<schema.table>', why="..."): the prompt lists only table names + a
   one-line purpose, so read a table's exact columns here before you write SQL.
   You may need MORE THAN ONE table (e.g. a ratio across two marts) — pull each.
3. extract(sql, name, purpose, why="..."): pull ALL the data you need in ONE
   WIDE extract — SELECT month + the metric columns, filtered to the entity,
   PLUS the mart's explanatory attribute columns (e.g. a type or band) when
   they could explain the metric, AGGREGATED IN SQL to month x entity x
   attribute grain so it stays small. Later analysis passes slice this same
   frame — never extract again between report passes. KEEP EVERY MONTH (never
   add a `WHERE <count> >= N` filter). The result is loaded as a pandas
   DataFrame named `name` (default `df`); call extract again with a different
   `name` only when a SECOND TABLE is genuinely needed (e.g. a ratio across two
   marts), then join in SQL or in pandas. You get up to {max_extracts}
   extracts. Use lookup_values first (FREE) to resolve a text value's exact
   spelling/casing. Do NOT spend an extract (or a turn) probing min/max month
   or row counts first — pull the series directly and read its span in pandas
   (`df[month_col].min()/.max()`) inside run_analysis.
4. run_analysis(code): write SHORT pandas that calls skills.* over the frame(s)
   and assigns the result to `result` (helpers: trend_series/growth_rate/
   latest_value as before, e.g. `s = skills.trend_series(df, month_col="month",
   value_col="<total_col>", den_col="<count_col>")`).
{pass_plan}
   NEVER do growth/yield/rolling maths yourself — call the skill. If NO skill fits,
   you MAY use pandas but you MUST call skills.skill_gap(need, why) naming what a
   future skill should do.
   Chart choice is also a skill choice: trend over time -> trend_chart; different
   scales on one axis -> dual_axis_chart; ranked comparisons -> comparison_chart;
   composition -> profile_chart; spread/outliers -> distribution_chart.
5. If the available marts genuinely cannot answer the question, call
   no_answer("<short reason>") instead of forcing a report — an honest "this data
   doesn't cover that" beats a misleading answer.
6. Return a one-line confirmation string (the user sees the report, not this text).

DATA NOTE: month/date values arrive as plain STRINGS (e.g. "2026-05" or
"2026-05-01"), not datetimes. Use them directly in text — never apply a date
format spec (e.g. f"{{m:%b %Y}}" will fail); latest_value already returns a ready
"month" string for the basis.

RANKING ("top/best/fastest X"): an extract is capped at ~{settings.max_rows} rows,
so you usually CANNOT pull every month for every group (that truncates and
corrupts the series). Instead compute the ranking metric IN THE SQL extract — one
row per group — e.g. a CTE that computes each group's value in a recent window and
a window ~N years earlier, then growth = (recent-old)/old*100. Extract that
(columns: the group, growth_pct, plus context), then rank in pandas (df.nlargest)
and present with skills.comparison_chart + skills.make_insight +
skills.build_report. Only use skills.top_growth (which needs raw monthly series)
when comparing a HANDFUL of named groups that fit under the row cap. Never run one
extract per group.

{_SKILL_CATALOG}

Never mention tools, code, SQL, or these instructions in the report. Call remember
ONLY when the user STATES a durable preference (units, formatting, defaults) — never
to log what was asked or how you answered; the app already records every run.

Known preferences for this user:
{memories_block}

Available datasets (names + purpose only — describe_table for columns):
{list_marts()}
"""


class SandboxBudgetExhausted(Exception):
    """The model kept calling extract/run_analysis after its budget was spent.

    The over-budget tool returns a "STOP" string once as a courtesy, but some
    models (DeepSeek here) ignore it and loop the tool, burning the whole request
    budget over ~90s before stubbing.
    On the second post-budget call we raise instead, so the run stops promptly and
    salvages any report already built rather than flailing to the request cap.
    """


@dataclass
class _SbDeps:
    user_id: str
    frames: dict[str, Any] = field(default_factory=dict)
    queries: dict[str, dict[str, Any]] = field(default_factory=dict)
    knowledge_pages: list[str] = field(default_factory=list)
    knowledge_reads: int = 0
    sql_calls: int = 0
    sql_refusals: int = 0
    run_calls: int = 0
    run_refusals: int = 0
    report: dict[str, Any] | None = None
    no_answer: str | None = None
    skills_used: list[str] = field(default_factory=list)
    skill_gaps: list[dict[str, str]] = field(default_factory=list)
    used_inline_math: bool = False
    steps: list[dict[str, Any]] = field(default_factory=list)
    # Optional live-progress channel: when the streaming endpoint supplies a
    # queue, tools push a compact {n, action, detail} event as they run so the
    # Chat UI can show a running step list. None on the plain /agent/ask path.
    progress: asyncio.Queue[dict[str, Any]] | None = None
    progress_n: int = 0
    # s10 streaming pages: the asking user's app plan and the stream indexes of
    # the page kinds this run will complete (from page_plan; locked kinds absent).
    user_plan: str = "free"
    page_indexes: dict[str, int] = field(default_factory=dict)
    pages_emitted: dict[str, dict[str, Any]] = field(default_factory=dict)

    def next_id(self, prefix: str, store: dict[str, Any]) -> str:
        return f"{prefix}{len(store) + 1}"

    def emit(self, action: str, detail: str = "") -> None:
        """Best-effort live-progress event; a no-op off the streaming path."""
        if self.progress is None:
            return
        self.progress_n += 1
        self.progress.put_nowait({"n": self.progress_n, "action": action, "detail": detail})

    def emit_frame(self, event: str, payload: dict[str, Any]) -> None:
        """plan/page frames ride the SAME progress queue; the SSE endpoint
        routes items carrying an ``event`` key to their own SSE event name.
        A no-op off the streaming path."""
        if self.progress is None:
            return
        self.progress.put_nowait({"event": event, **payload})

    def emit_page(self, kind: str, page: dict[str, Any]) -> None:
        """Stream one finished page (validated Template Studio Page JSON).

        A full no-op off the streaming path — pages_emitted and the page_emit
        trace step only ever record what was actually streamed.
        """
        if self.progress is None:
            return
        index = self.page_indexes.get(kind)
        if index is None:  # not in this user's plan
            return
        first_emit = kind not in self.pages_emitted
        self.pages_emitted[kind] = page
        self.emit_frame("page", {"index": index, "kind": kind, "status": "complete", "page": page})
        if first_emit:  # record streaming in the trace (a retry re-emit isn't a new step)
            self.steps.append(
                {"kind": "page_emit", "status": "success", "page": kind, "index": index}
            )

    def emit_skipped_pages(self) -> None:
        """Tell the client planned-but-unproduced pages won't arrive (clears ghosts)."""
        for kind, index in self.page_indexes.items():
            if kind not in self.pages_emitted:
                self.emit_frame("page", {"index": index, "kind": kind, "status": "skipped"})


async def answer_with_sandbox(
    question: str,
    *,
    user_id: str,
    plan: str = "free",
    progress: asyncio.Queue[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Run the sandbox agent path; None to fall back to the offline stub.

    When the LLM ran but never completed a report, the return value is a
    *salvage* dict (``{"fallback": True, steps, input_tokens, output_tokens}``)
    instead of None: the caller still falls back to the stub answer, but the
    model turns, tool calls and token consumption that were actually spent stay
    visible in the trace (app.query_runs + the chat "agent run" expander).

    When ``progress`` is supplied (the streaming endpoint), tools push live
    step events onto it as they run.
    """
    if not _PYDANTIC_AI_AVAILABLE:
        return None
    selected = choose_provider(
        settings.llm_provider, settings.deepseek_api_key, settings.anthropic_api_key
    )
    if selected is None:
        return None
    provider, api_key = selected
    deps: _SbDeps | None = None
    captured: list[Any] = []
    try:
        os.environ.setdefault(_ENV_VAR[provider], api_key)
        # The page plan is deterministic policy per user (s10): declare it first
        # so the frontend can draw the ghost slots before any model work starts.
        deps = _SbDeps(user_id=user_id, progress=progress, user_plan=plan)
        plan_slots = page_plan(plan=plan)
        deps.page_indexes = {s["kind"]: s["index"] for s in plan_slots if s["status"] != "locked"}
        deps.emit_frame("plan", {"pages": plan_slots})

        recalled = await recall_memories(user_id, question)
        model_name = settings.deepseek_model if provider == "deepseek" else settings.model
        max_extracts = settings.max_sql_attempts
        max_runs = settings.sandbox_run_attempts
        agent: Agent[_SbDeps, str] = Agent(
            f"{provider}:{model_name}",
            deps_type=_SbDeps,
            output_type=str,
            system_prompt=_sandbox_system_prompt(
                recalled,
                max_extracts,
                max_runs,
                include_insights="insights" in deps.page_indexes,
            ),
            retries=3,
        )
        _register_sandbox_tools(agent, max_extracts, max_runs)
        usage_limits = UsageLimits(
            request_limit=settings.agent_request_limit,
            total_tokens_limit=settings.agent_total_tokens_limit,
        )
        with capture_run_messages() as messages:
            captured = messages
            try:
                await agent.run(question, deps=deps, usage_limits=usage_limits)
            except Exception as exc:  # noqa: BLE001 — salvage any report already built
                if deps.report is None and deps.no_answer is None:
                    raise
                print(f"[data-agent] sandbox run errored ({exc}); using result built so far")

        if deps.report is None:
            if deps.no_answer:
                # The agent judged the marts can't answer this — return an honest
                # "no answer" report instead of falling through to a domain stub.
                return _no_answer_result(deps, messages, provider)
            # Model never produced a report — fall back to the stub, but keep
            # the LLM turns + token spend visible in the trace.
            return _salvage_fallback(captured, deps, "model never produced a report")

        report = {
            **deps.report,
            "queries": _query_list(deps.queries),
            "knowledge_pages_used": deps.knowledge_pages,
            "knowledge_version": knowledge_version(),
        }
        # Pages contract (s07): compose Summary → Insights pages from the governed
        # report objects. The composition steps join deps.steps BEFORE the decision
        # log is built, so admins see object-build → template-pick → page-compose
        # both as raw steps and as decisions in app.query_runs.
        pages, page_steps = compose_pages(report, question=question)
        # s10: the result honours the user's plan too — pages above it are
        # locked teasers on the wire, never delivered content.
        allowed_kinds = set(planned_kinds(plan))
        pages = [p for p in pages if p.get("kind", p["template"]) in allowed_kinds]
        if pages:
            report["pages"] = pages
        deps.steps.extend(page_steps)
        # Stream any page the tool path didn't already emit (e.g. a model that
        # built everything in one pass before the emit hooks fired), then tell
        # the client which planned pages won't arrive so its ghosts clear.
        for p in pages:
            kind = p.get("kind", p["template"])
            if kind not in deps.pages_emitted:
                deps.emit_page(kind, p)
        deps.emit_skipped_pages()

        trace = _merge_decision_log(_build_trace(messages), deps.steps)
        trace.extend(page_steps)
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

        primary = select_primary_query(deps.queries)
        return {
            "answer": report.get("summary", ""),
            "report": report,
            "pages": pages or None,
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
        if deps is not None and captured:
            # The LLM ran before failing — surface its trace with the stub answer.
            return _salvage_fallback(captured, deps, str(exc))
        return None


def _salvage_fallback(messages: list[Any], deps: _SbDeps, why: str) -> dict[str, Any]:
    """Package a failed sandbox run's trace so the stub fallback can keep it.

    The caller (main._answer) still answers with the deterministic stub; this
    preserves what the model actually did — its turns (input/output), tool
    calls and token consumption — plus a ``fallback`` step naming the reason,
    so admins can diagnose why the run fell back.
    """
    trace = _merge_decision_log(_build_trace(messages), deps.steps)
    trace.append({"kind": "fallback", "status": "error", "error": why, "to": "stub"})
    model_steps = [s for s in trace if s["kind"] == "model"]
    return {
        "fallback": True,
        "steps": trace,
        "input_tokens": sum(s.get("input_tokens") or 0 for s in model_steps) or None,
        "output_tokens": sum(s.get("output_tokens") or 0 for s in model_steps) or None,
    }


def _no_answer_result(deps: _SbDeps, messages: list[Any], provider: str) -> dict[str, Any]:
    """Shape an honest 'this data can't answer that' response (report-compatible).

    Keeps the same envelope the frontend expects (a report with an empty body and
    a ``no_answer`` flag), so the UI can render the reason instead of the app
    silently falling back to a domain-specific stub.
    """
    deps.emit_skipped_pages()  # no pages will arrive — clear any ghost slots
    trace = _merge_decision_log(_build_trace(messages), deps.steps)
    report = {
        "element_id": "report",
        "summary": deps.no_answer or "The available data can't answer that question.",
        "headlines": [],
        "insights": [],
        "profiles": [],
        "main_chart": None,
        "queries": _query_list(deps.queries),
        "knowledge_pages_used": deps.knowledge_pages,
        "knowledge_version": knowledge_version(),
        "no_answer": True,
    }
    return {
        "answer": report["summary"],
        "report": report,
        "sql": None,
        "columns": [],
        "rows": [],
        "row_count": 0,
        "chart": None,
        "engine": provider,
        "input_tokens": None,
        "output_tokens": None,
        "steps": trace,
    }


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


def _merge_decision_log(
    trace: list[dict[str, Any]], steps: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    decisions = _decision_log(steps)
    if decisions:
        trace.append({"kind": "decision_log", "decisions": decisions})
    return trace


def _decision_log(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Condense tool-side telemetry into the evaluable decisions for this run."""
    decisions: list[dict[str, Any]] = []
    for step in steps:
        kind = step.get("kind")
        why = step.get("why") or step.get("purpose") or ""
        status = step.get("status")
        if kind == "knowledge":
            decisions.append(
                {
                    "type": "knowledge",
                    "choice": step.get("name"),
                    "why": why,
                    "status": status,
                }
            )
        elif kind == "schema":
            decisions.append(
                {
                    "type": "table",
                    "choice": step.get("table"),
                    "why": why,
                    "status": status,
                }
            )
        elif kind == "lookup":
            decisions.append(
                {
                    "type": "lookup",
                    "choice": f"{step.get('table')}.{step.get('column')}",
                    "why": why,
                    "status": status,
                }
            )
        elif kind == "sql":
            choice = step.get("ref") or "extract"
            decisions.append(
                {
                    "type": "sql",
                    "choice": choice,
                    "why": why,
                    "status": status,
                    "row_count": step.get("row_count"),
                    "sql": step.get("sql"),
                }
            )
        elif kind == "analysis":
            for skill_name in step.get("skills_used") or []:
                decisions.append(
                    {
                        "type": "skill",
                        "choice": skill_name,
                        "why": why or "selected inside run_analysis",
                        "status": status,
                    }
                )
                if str(skill_name).endswith("_chart"):
                    decisions.append(
                        {
                            "type": "chart",
                            "choice": skill_name,
                            "why": why or "chart skill used in report",
                            "status": status,
                        }
                    )
            for gap in step.get("skill_gaps") or []:
                decisions.append(
                    {
                        "type": "skill_gap",
                        "choice": gap.get("need"),
                        "why": gap.get("why") or why,
                        "status": status,
                    }
                )
        elif kind == "template_pick":
            decisions.append(
                {
                    "type": "template",
                    "choice": step.get("template"),
                    "why": why,
                    "status": status,
                }
            )
        elif kind == "page_compose":
            templates = step.get("templates") or []
            decisions.append(
                {
                    "type": "pages",
                    "choice": " + ".join(templates) if templates else None,
                    "why": why,
                    "status": status,
                }
            )
        elif kind == "no_answer":
            decisions.append(
                {
                    "type": "no_answer",
                    "choice": step.get("reason"),
                    "why": why,
                    "status": status,
                }
            )
    return [
        {k: v for k, v in {**d, "order": i + 1}.items() if v not in (None, "")}
        for i, d in enumerate(decisions)
    ]


def _register_sandbox_tools(agent: Agent[_SbDeps, str], max_extracts: int, max_runs: int) -> None:
    @agent.tool(name="search_knowledge")
    async def search_knowledge_tool(ctx: RunContext[_SbDeps], query: str, why: str = "") -> str:
        """Search the Insight Playbook for pages relevant to the question."""
        ctx.deps.emit("Searching knowledge", query)
        text, inlined = search_knowledge_result(query)
        for name in inlined:
            if name not in ctx.deps.knowledge_pages:
                ctx.deps.knowledge_pages.append(name)
                ctx.deps.knowledge_reads += 1
                ctx.deps.steps.append(
                    {"kind": "knowledge", "status": "inlined", "name": name, "why": why}
                )
        return text

    @agent.tool(name="read_knowledge")
    async def read_knowledge_tool(ctx: RunContext[_SbDeps], name: str, why: str = "") -> str:
        """Load the full body of a knowledge page by name."""
        if name in ctx.deps.knowledge_pages:
            return f"(already loaded '{name}' earlier — see above.)"
        if ctx.deps.knowledge_reads >= settings.max_knowledge_reads:
            return "knowledge read limit reached; proceed with the pages you have."
        ctx.deps.emit("Reading knowledge", name)
        ctx.deps.knowledge_pages.append(name)
        ctx.deps.knowledge_reads += 1
        ctx.deps.steps.append({"kind": "knowledge", "status": "read", "name": name, "why": why})
        return read_knowledge(name)

    @agent.tool(name="describe_table")
    async def describe_table_tool(ctx: RunContext[_SbDeps], table: str, why: str = "") -> str:
        """Full column-level docs for one table (schema.table)."""
        ctx.deps.emit("Inspecting schema", table)
        ctx.deps.steps.append({"kind": "schema", "status": "described", "table": table, "why": why})
        return describe_table(table)

    @agent.tool
    async def lookup_values(
        ctx: RunContext[_SbDeps],
        column: str,
        pattern: str,
        table: str,
        why: str = "",
    ) -> str:
        """Resolve exact distinct values of a text column (e.g. a name's casing). FREE.

        Pass the schema-qualified table the column lives in (see the mart index /
        describe_table). Dataset-agnostic — no default table. Resolve SEVERAL
        values in ONE call with `|` alternation (e.g. pattern="Normanhurst|Hornsby");
        matching is case-insensitive contains, so plain words work — never retry
        with different casing.
        """
        ctx.deps.emit("Resolving values", f"{table}.{column}")
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
            {"kind": "lookup", "table": table, "column": column, "values": values, "why": why}
        )
        return json.dumps({"column": column, "matches": values})

    # NOTE: no list_skills tool — the full catalog is already in the system
    # prompt verbatim; a tool for it just tempted the model into a wasted turn.

    @agent.tool(sequential=True)
    async def extract(
        ctx: RunContext[_SbDeps],
        sql: str,
        name: str = "df",
        purpose: str = "",
        why: str = "",
    ) -> str:
        """Run a governed SELECT; the result is loaded as a pandas DataFrame `name`."""
        if ctx.deps.sql_calls >= max_extracts:
            ctx.deps.sql_refusals += 1
            if ctx.deps.sql_refusals > 1:
                raise SandboxBudgetExhausted(
                    f"extract called after the {max_extracts}-attempt budget was spent"
                )
            return "STOP: no extract attempts left. Analyse the frames you have."
        ctx.deps.sql_calls += 1
        remaining = max_extracts - ctx.deps.sql_calls
        ctx.deps.emit("Querying data", purpose or f"attempt {ctx.deps.sql_calls}")
        try:
            frame, result = await run_extract(sql, user_id=ctx.deps.user_id)
        except Exception as exc:  # noqa: BLE001 — let the model self-correct
            ctx.deps.steps.append(
                {
                    "kind": "sql",
                    "sql": sql,
                    "status": "error",
                    "error": str(exc),
                    "purpose": purpose,
                    "why": why,
                }
            )
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
                "purpose": purpose,
                "why": why,
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
    async def run_analysis(ctx: RunContext[_SbDeps], code: str, why: str = "") -> str:
        """Execute pandas over the extracted frame(s) in the sandbox; calls skills.*.

        Assign the finished report to `result` (skills.build_report(...)). Returns
        the skills used on success, or the error to fix. Prefer skills; if none
        fits you MAY use pandas but MUST call skills.skill_gap(need, why).
        """
        if not ctx.deps.frames:
            return "no data yet — call extract(sql) first to load a DataFrame."
        if ctx.deps.run_calls >= max_runs:
            ctx.deps.run_refusals += 1
            if ctx.deps.report is None:
                raise SandboxBudgetExhausted(
                    "run_analysis budget was spent before a report was built. "
                    "The analysis code must assign `result = skills.build_report(...)`."
                )
            if ctx.deps.run_refusals > 1:
                raise SandboxBudgetExhausted(
                    f"run_analysis called after the {max_runs}-attempt budget was spent"
                )
            return "STOP: no run_analysis attempts left. Use the report already built."
        ctx.deps.run_calls += 1
        ctx.deps.emit("Building the report", "")
        result = run_code(code, frames=ctx.deps.frames)
        # Accumulate across passes — pass 2's telemetry must not erase pass 1's.
        for name in result.skills_used:
            if name not in ctx.deps.skills_used:
                ctx.deps.skills_used.append(name)
        ctx.deps.skill_gaps.extend(g.model_dump() for g in result.skill_gaps)
        ctx.deps.used_inline_math = ctx.deps.used_inline_math or result.used_inline_math
        ctx.deps.steps.append(
            {
                "kind": "analysis",
                "status": "error" if result.error else "ok",
                "skills_used": result.skills_used,
                "skill_gaps": [g.model_dump() for g in result.skill_gaps],
                "error": result.error,
                "why": why,
            }
        )
        if result.error:
            return f"run_analysis error (fix and retry): {result.error}"
        if result.report is None:
            missing_report = (
                "sandbox code completed but did not assign a report dict to `result`. "
                "Fix the code and end with `result = skills.build_report(...)`."
            )
            ctx.deps.steps[-1]["status"] = "error"
            ctx.deps.steps[-1]["error"] = missing_report
            return f"run_analysis error (fix and retry): {missing_report}"
        gap_note = f" Skill gaps recorded: {ctx.deps.skill_gaps}." if ctx.deps.skill_gaps else ""

        # --- PASS 2: an insights patch merges into the pass-1 report ---------
        if result.report.get("element_id") == "insights_patch":
            if ctx.deps.report is None:
                missing_pass1 = (
                    "build_insights ran before build_report: run PASS 1 first "
                    "(result = skills.build_report(...)), then add insights."
                )
                ctx.deps.steps[-1]["status"] = "error"
                ctx.deps.steps[-1]["error"] = missing_pass1
                return f"run_analysis error (fix and retry): {missing_pass1}"
            ctx.deps.report["insights"] = result.report.get("insights") or []
            if result.report.get("profiles"):
                ctx.deps.report["profiles"] = result.report["profiles"]
            page, _ = compose_insights_page(ctx.deps.report)
            if page is not None:
                ctx.deps.emit("Streaming page 2", "insights")
                ctx.deps.emit_page("insights", page)
            return (
                f"insights merged into the report. Skills used: {result.skills_used}."
                f"{gap_note} Now return a one-line confirmation."
            )

        # --- PASS 1 (or a single-pass full report) ---------------------------
        ctx.deps.report = result.report
        page, _ = compose_summary_page(ctx.deps.report)
        if page is not None:
            ctx.deps.emit("Streaming page 1", "summary")
            ctx.deps.emit_page("summary", page)
        # A single-pass model may have included insights already — stream them.
        if ctx.deps.report.get("insights") or ctx.deps.report.get("profiles"):
            page2, _ = compose_insights_page(ctx.deps.report)
            if page2 is not None:
                ctx.deps.emit_page("insights", page2)
        elif "insights" in ctx.deps.page_indexes:
            return (
                f"report built — Page 1 is streaming. Skills used: {result.skills_used}."
                f"{gap_note} Now run PASS 2: slice the same frame by its attribute "
                "columns and assign result = skills.build_insights(insights=[...]) "
                "to explain the headline. Do NOT extract again."
            )
        return (
            f"report built. Skills used: {result.skills_used}.{gap_note} "
            "Now return a one-line confirmation."
        )

    @agent.tool
    async def no_answer(ctx: RunContext[_SbDeps], reason: str, why: str = "") -> str:
        """Declare that the available marts can't answer this question.

        Use when no dataset covers what's asked (wrong domain, missing metric or
        dimension). Records an honest reason instead of forcing a misleading
        report. Give a short, user-facing reason (what's missing / what the data
        does cover). Then return a one-line confirmation.
        """
        ctx.deps.emit("Concluding", "data can't answer this")
        ctx.deps.no_answer = reason
        ctx.deps.steps.append({"kind": "no_answer", "reason": reason, "why": why})
        return "recorded no_answer; now return a one-line confirmation."

    @agent.tool
    async def remember(ctx: RunContext[_SbDeps], fact: str) -> str:
        """Store a durable user preference about how they want answers."""
        ctx.deps.emit("Saving preference", fact)
        await remember_memory(ctx.deps.user_id, fact)
        ctx.deps.steps.append({"kind": "memory", "status": "saved", "fact": fact})
        return "remembered"
