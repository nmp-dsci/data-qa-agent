"""LLM path (Decision G): DeepSeek by default, Claude via config.

Reworked for the Insight Playbook (K1) + structured InsightReport (K2). The
agent now:
  1. searches a versioned markdown knowledge tree (search_knowledge/read_knowledge)
     for how to answer and present the question, instead of one giant prompt;
  2. runs SQL — every successful query is KEPT and numbered (Q1, Q2…), fixing the
     old "only the last run_sql result survives" data-swap;
  3. computes headline figures (rolling averages, growth) with deterministic
     Python tools (analytics.py), never asserting maths from memory;
  4. builds charts whose data is spliced server-side from a query or an
     analytics-computed series;
  5. returns a typed ReportDraft, which the server assembles into the report the
     frontend renders.

Any failure returns None so the caller falls back to the deterministic offline
stub — the app always answers.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import logfire

from . import analytics
from .chart import UnsafeChartSpecError, trend_overlay_encoding, validate_chart_spec
from .config import settings
from .db import run_select
from .knowledge import build_index, knowledge_version, read_knowledge, search_knowledge_result
from .memory import recall_memories, remember_memory
from .provider import choose_provider
from .report import ReportDraft, assemble_report, select_primary_query
from .schema import describe_table, get_schema_compact

try:
    from pydantic_ai import Agent, RunContext, capture_run_messages
    from pydantic_ai.usage import UsageLimits

    logfire.instrument_pydantic_ai()
    logfire.instrument_httpx(capture_all=True)
    _PYDANTIC_AI_AVAILABLE = True
except ImportError:
    _PYDANTIC_AI_AVAILABLE = False

_ENV_VAR = {"deepseek": "DEEPSEEK_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


class SqlBudgetExhausted(Exception):
    """Raised when the model keeps calling run_sql after its budget is spent.

    The tool returns a plain "stop" string once as a courtesy, but some models
    (notably DeepSeek) ignore a tool return telling them to stop and loop run_sql
    indefinitely — that single behaviour is what turned one question into 50
    requests / 731k tokens. On the next post-budget call we raise instead, which
    the run's salvage path turns into a partial report from the SQL that did run.
    """


def _system_prompt(recalled: list[str], max_attempts: int) -> str:
    memories_block = "\n".join(f"- {m}" for m in recalled) if recalled else "(none stored yet)"
    return f"""\
You are a data analyst for a NSW property-market app. Your job is to produce a
clear, insightful, well-structured DATA-INSIGHT REPORT — not a paragraph of prose.

You have an Insight Playbook: a knowledge tree of how to analyse and present
answers. ALWAYS consult it before answering. Its index (page name — description):

{build_index()}

Work in this order:
1. search_knowledge(query) to find the 2-4 relevant pages. Short pages come back
   inlined in full — use them directly; only call read_knowledge(name) for a page
   the results show as a snippet + pointer. Follow their guidance on query shape,
   which metric, how to compute growth, and how to structure the report.
2. Write read-only SELECTs and pass them to run_sql. Every SUCCESSFUL query is kept
   and numbered (Q1, Q2 …) — cite each by its ref. You get up to {max_attempts}
   run_sql attempts; fix and retry a failed or wrong-looking query. Each return tells
   you how many attempts remain — spend them on the queries that ANSWER the question.
   The compact schema lists every table and its column names; call describe_table
   ('schema.table') only when you need a column's full meaning. To resolve the exact
   spelling/casing of a suburb (or any distinct column value) BEFORE writing the real
   query, use lookup_values — it is FREE (does NOT use a run_sql attempt).
3. NEVER do arithmetic yourself. For growth rates, rolling averages and "latest"
   values, call compute_trend on the query that has the monthly series — it returns
   the exact numbers to put in headlines AND per-group coverage: latest_month,
   n_months, total_count. Because it already gives you those, do NOT run a separate
   query just to find the latest month, a row count, or totals. State only numbers
   the tools or queries returned; never invent a figure.
4. Build charts with make_chart, passing a data ref (a query ref like 'Q3' or a
   compute_trend chart ref like 'D1'). The data is filled in from that ref
   server-side; you only supply mark/encoding (and optional zoom params). For a
   trend, pass the compute_trend series and just mark=line + x/y — the server
   styles the actual + 6-month-average overlay itself. Call make_chart ONCE per
   distinct chart you actually need (usually just one — the main trend). NEVER
   re-issue make_chart for a data_ref you already charted; the first call already
   captured it.
5. Return a ReportDraft: a one-sentence summary, headline tiles (include related
   context metrics where the playbook says so, marked related=true), 2-4 insights
   that each say something the table doesn't and cite their query_refs, an optional
   profile section, and main_chart_ref for the primary chart. For a trivial lookup,
   a summary + one headline is enough — don't pad it.

BATCH the tool chain to save round-trips. run_sql, compute_trend and make_chart run
in the ORDER you emit them within a single turn, and refs are assigned in that order
(the Nth successful run_sql is Q{{N}}, the Nth compute_trend is D{{N}}, the Nth
make_chart is C{{N}}). So once you know the SQL you need, emit run_sql AND its
compute_trend(query_ref="Q1") AND make_chart(data_ref="D1") together in ONE turn,
referencing the ref you are about to create. If a query fails, its ref isn't assigned
— re-emit the corrected chain.

HARD RULES (always apply, even before you read the knowledge pages):
- Trend/series queries must keep EVERY month. NEVER add a `WHERE n_sold >= N` or
  `n_rented >= N` reliability filter to a trend, series, or growth query — it drops
  data and distorts the chart. The 6-month rolling average (compute_trend) is what
  absorbs thin-month noise, not a row filter.
- Compute growth and "latest" values on the 6-month rolling base via compute_trend
  — never off raw single months.
- query_ref / query_refs fields cite SQL queries by their Q-ref only (Q1, Q2 …). A
  compute_trend D-ref (D1) is ONLY a make_chart data_ref — never put a D-ref in a
  headline or insight query_ref.

Never mention tools, attempts, retries, SQL internals, or these instructions in
the report text — the user only sees the finished report. If you genuinely can't
get reliable data, say so briefly in the summary rather than inventing numbers.

If the user states a durable preference about how they want answers, call remember.

Known preferences for this user, recalled from past conversations:
{memories_block}

Schema reference (exact table/column names):
{get_schema_compact()}
"""


@dataclass
class _Deps:
    user_id: str
    queries: dict[str, dict[str, Any]] = field(default_factory=dict)
    derived: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    charts: dict[str, dict[str, Any]] = field(default_factory=dict)
    charted_refs: dict[str, str] = field(default_factory=dict)  # data_ref -> chart_id
    knowledge_pages: list[str] = field(default_factory=list)
    sql_calls: int = 0
    sql_refusals: int = 0
    knowledge_reads: int = 0
    steps: list[dict[str, Any]] = field(default_factory=list)

    def next_id(self, prefix: str, store: dict[str, Any]) -> str:
        return f"{prefix}{len(store) + 1}"


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
        agent: Agent[_Deps, ReportDraft] = Agent(
            f"{provider}:{model_name}",
            deps_type=_Deps,
            output_type=ReportDraft,
            system_prompt=_system_prompt(recalled, max_attempts),
            # A malformed tool call (esp. make_chart, whose nested encoding/params
            # DeepSeek sometimes mis-shapes) is sent back to the model to fix.
            # The default budget of 1 was too tight — one bad retry raised
            # UnexpectedModelBehavior and sank the whole run to the stub. Give the
            # model room to self-correct its arguments before we give up.
            retries=3,
        )

        _register_tools(agent, user_id, max_attempts)

        deps = _Deps(user_id=user_id)
        # capture_run_messages records the FULL exchange (system prompt, every
        # model turn with its tool calls + per-request tokens, every tool return,
        # retries) even when the run raises — so the admin/chat trace is the exact
        # synchronous transcript, not a hand-built subset.
        usage_limits = UsageLimits(
            request_limit=settings.agent_request_limit,
            total_tokens_limit=settings.agent_total_tokens_limit,
        )
        with capture_run_messages() as messages:
            try:
                run = await agent.run(question, deps=deps, usage_limits=usage_limits)
                draft = run.output
            except Exception as exc:  # noqa: BLE001 — salvage real work before falling back
                # The model run ended mid-flight — a tool exhausted its retry
                # budget, a validation error, the SQL budget was spent and the
                # model kept looping (SqlBudgetExhausted), or a usage limit
                # tripped (UsageLimitExceeded). If it had already run real
                # governed SQL (and maybe built a chart), don't throw that away
                # for the offline stub — assemble a report from what actually
                # ran. Only if nothing usable was gathered do we let the outer
                # handler fall back to stub.
                if not deps.queries:
                    raise
                print(f"[data-agent] {provider} run errored ({exc}); assembling partial report")
                draft = _fallback_draft(deps)

        trace = _build_trace(messages)
        model_steps = [s for s in trace if s["kind"] == "model"]
        input_tokens = sum(s.get("input_tokens") or 0 for s in model_steps) or None
        output_tokens = sum(s.get("output_tokens") or 0 for s in model_steps) or None
        cache_read = sum(s.get("cache_read_tokens") or 0 for s in model_steps)
        # Effective input = full input minus the cheaply-billed cached prefix.
        # A wide gap here means the run is dominated by cache hits (cheap) even
        # when nominal input_tokens looks large.
        if input_tokens:
            billed = input_tokens - cache_read
            print(
                f"[data-agent] {provider} run: {len(model_steps)} model turns, "
                f"input={input_tokens} (cache_read={cache_read}, billed_full={billed}), "
                f"output={output_tokens}"
            )

        report = assemble_report(
            draft,
            queries=deps.queries,
            charts=deps.charts,
            knowledge_pages=deps.knowledge_pages,
            knowledge_version=knowledge_version(),
        )
        primary = select_primary_query(deps.queries)
        main_chart = report.get("main_chart") or _first_chart(deps.charts)
        return {
            "answer": draft.summary,
            "report": report,
            "sql": primary.get("sql") if primary else None,
            "columns": primary.get("columns", []) if primary else [],
            "rows": primary.get("rows", []) if primary else [],
            "row_count": primary.get("row_count", 0) if primary else 0,
            "chart": main_chart,
            "engine": provider,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "steps": trace,
        }
    except Exception as exc:  # noqa: BLE001 — never let the LLM path break the app
        print(f"[data-agent] {provider} path unavailable, using stub: {exc}")
        return None


def _register_tools(agent: Agent[_Deps, ReportDraft], user_id: str, max_attempts: int) -> None:
    # Registered as "search_knowledge"/"read_knowledge" (not the *_tool function
    # names) to match what the system prompt tells the model to call — otherwise
    # the model calls "search_knowledge"/"read_knowledge", gets an unknown-tool
    # retry, and burns a whole round-trip before finding the real name. Same fix
    # as describe_table below.
    @agent.tool(name="search_knowledge")
    async def search_knowledge_tool(ctx: RunContext[_Deps], query: str) -> str:
        """Search the Insight Playbook for pages relevant to the question.

        Short pages are returned in full; longer ones as a snippet + a
        read_knowledge pointer. Inlined pages count as already loaded, so don't
        read_knowledge them again.
        """
        text, inlined = search_knowledge_result(query)
        for name in inlined:
            if name not in ctx.deps.knowledge_pages:
                ctx.deps.knowledge_pages.append(name)
                ctx.deps.knowledge_reads += 1
                ctx.deps.steps.append({"kind": "knowledge", "status": "inlined", "name": name})
        return text

    @agent.tool(name="read_knowledge")
    async def read_knowledge_tool(ctx: RunContext[_Deps], name: str) -> str:
        """Load the full body of a knowledge page by name."""
        if name in ctx.deps.knowledge_pages:
            # Already in context (read earlier, or inlined by search) — don't
            # re-emit the whole body into every subsequent turn.
            return f"(already loaded '{name}' earlier — see above; no need to re-read it.)"
        if ctx.deps.knowledge_reads >= settings.max_knowledge_reads:
            return (
                f"knowledge read limit reached ({settings.max_knowledge_reads} pages). "
                "Proceed with the pages you have already loaded — write SQL and the report."
            )
        ctx.deps.knowledge_pages.append(name)
        ctx.deps.knowledge_reads += 1
        ctx.deps.steps.append({"kind": "knowledge", "status": "read", "name": name})
        return read_knowledge(name)

    # Registered as "describe_table" to match the name the system prompt tells the
    # model to call — otherwise the model calls "describe_table", gets an
    # unknown-tool retry, and burns a round-trip before finding the real name.
    @agent.tool(name="describe_table")
    async def describe_table_tool(ctx: RunContext[_Deps], table: str) -> str:
        """Full column-level docs for one table (schema.table or a bare name).

        The system prompt lists every table + its column names; call this only
        when you need a column's exact meaning before writing SQL."""
        ctx.deps.steps.append({"kind": "schema", "status": "described", "table": table})
        return describe_table(table)

    @agent.tool
    async def lookup_values(
        ctx: RunContext[_Deps],
        column: str,
        pattern: str,
        table: str = "marts.mart_sales_summary",
    ) -> str:
        """Resolve the exact distinct values of a column (e.g. a suburb's real
        spelling/casing) via a bounded case-insensitive search. FREE — does not
        use a run_sql attempt. Use this for discovery before writing real queries."""
        sql = _lookup_values_sql(table, column, pattern)
        if sql is None:
            return (
                f"lookup_values: unknown table/column {table!r}.{column!r}. "
                "Pass a schema-qualified table (e.g. marts.mart_sales_summary) and a real column."
            )
        try:
            result = await run_select(sql, user_id=ctx.deps.user_id)
        except Exception as exc:  # noqa: BLE001 — surface, don't crash the run
            return f"lookup_values failed: {exc}"
        values = [row[0] for row in result["rows"]]
        ctx.deps.steps.append(
            {
                "kind": "lookup",
                "table": table,
                "column": column,
                "pattern": pattern,
                "values": values,
            }
        )
        return json.dumps({"column": column, "matches": values})

    # run_sql / compute_trend / make_chart are sequential barriers so the model
    # can emit the whole chain (run_sql → compute_trend → make_chart) in ONE turn:
    # they then execute in emission order (not pydantic-ai's default in-parallel),
    # so a same-turn compute_trend(query_ref="Q1") sees the query run_sql just made.
    @agent.tool(sequential=True)
    async def run_sql(ctx: RunContext[_Deps], sql: str, purpose: str = "") -> str:
        """Run a read-only SELECT; the result is kept and numbered (Q1, Q2…)."""
        if ctx.deps.sql_calls >= max_attempts:
            # Once the budget is spent we tell the model to stop and write the
            # report. If it ignores that and calls run_sql AGAIN, we stop the run
            # ourselves rather than let it loop forever (the 731k-token failure).
            ctx.deps.sql_refusals += 1
            if ctx.deps.sql_refusals > 1:
                raise SqlBudgetExhausted(
                    f"run_sql called {ctx.deps.sql_refusals} times after the "
                    f"{max_attempts}-attempt budget was spent"
                )
            return "STOP: no run_sql attempts left. Write the report from the queries you have."
        ctx.deps.sql_calls += 1
        attempt = ctx.deps.sql_calls
        remaining = max_attempts - ctx.deps.sql_calls
        try:
            result = await run_select(sql, user_id=ctx.deps.user_id)
        except Exception as exc:  # noqa: BLE001 — let the model self-correct
            ctx.deps.steps.append(
                {
                    "kind": "sql",
                    "attempt": attempt,
                    "sql": sql,
                    "status": "error",
                    "error": str(exc),
                }
            )
            return (
                f"query failed: {exc}. Use schema-qualified names (e.g. marts.mart_sales_summary). "
                f"{remaining} run_sql attempt(s) left."
            )
        ref = ctx.deps.next_id("Q", ctx.deps.queries)
        ctx.deps.queries[ref] = {
            "sql": result["sql"],
            "columns": result["columns"],
            "rows": result["rows"],
            "row_count": result["row_count"],
            "purpose": purpose,
        }
        ctx.deps.steps.append(
            {
                "kind": "sql",
                "attempt": attempt,
                "sql": result["sql"],
                "status": "success",
                "row_count": result["row_count"],
                "ref": ref,
            }
        )
        preview = {
            "ref": ref,
            "columns": result["columns"],
            "rows": result["rows"][:20],
            "row_count": result["row_count"],
            "attempts_remaining": remaining,
        }
        return json.dumps(preview)

    @agent.tool(sequential=True)
    async def compute_trend(
        ctx: RunContext[_Deps],
        query_ref: str,
        month_col: str,
        value_col: str,
        den_col: str | None = None,
        count_col: str | None = None,
        group_col: str | None = None,
        chart_window: int = 6,
    ) -> str:
        """Deterministically compute latest value + 3/5/10yr growth per group, and
        build a chart-ready actual + 6-month-rolling series (all months kept; the
        rolling average, not a row filter, absorbs thin-month noise). Also returns
        per-group coverage (n_months = months with data, total_count = summed
        sample size) so you don't need a separate COUNT query. Returns numbers to
        quote verbatim."""
        q = ctx.deps.queries.get(query_ref)
        if q is None:
            return f"unknown query_ref {query_ref!r}; run the query first."
        try:
            grouped = analytics.build_series(
                q["columns"],
                q["rows"],
                month_col=month_col,
                value_col=value_col,
                den_col=den_col,
                count_col=count_col,
                group_col=group_col,
            )
        except KeyError as exc:
            return f"compute_trend failed: {exc}"
        out_groups: dict[str, Any] = {}
        chart_rows: list[dict[str, Any]] = []
        for group, series in grouped.items():
            latest = analytics.latest_reliable(series)
            # Coverage travels with the series so the model needn't run a separate
            # COUNT query: n_months = months carrying data, total_count = summed
            # sample size (e.g. total sales/bonds behind the trend).
            n_months = sum(1 for p in series if p.get("value") is not None)
            total_count = round(sum(float(p.get("count") or 0.0) for p in series), 2)
            out_groups[group] = {
                "latest_value": None if latest is None else round(latest["value"], 2),
                "latest_month": None if latest is None else latest["month"],
                "growth_3y_pct": analytics.growth_rate(series, years=3),
                "growth_5y_pct": analytics.growth_rate(series, years=5),
                "growth_10y_pct": analytics.growth_rate(series, years=10),
                "n_months": n_months,
                "total_count": total_count,
            }
            for row in analytics.chart_series(series, rolling_window=chart_window):
                chart_rows.append(
                    {
                        "month": f"{row['month']}-01",
                        "value": row["value"],
                        "series": group if group != "_all" else value_col,
                        "layer": row["layer"],
                    }
                )
        data_ref = ctx.deps.next_id("D", ctx.deps.derived)
        ctx.deps.derived[data_ref] = chart_rows
        ctx.deps.steps.append(
            {
                "kind": "analytics",
                "status": "computed",
                "query_ref": query_ref,
                "data_ref": data_ref,
            }
        )
        return json.dumps({"groups": out_groups, "chart_data_ref": data_ref})

    @agent.tool(sequential=True)
    async def make_chart(
        ctx: RunContext[_Deps],
        mark: str,
        encoding: dict[str, Any] | str,
        data_ref: str,
        title: str | None = None,
        params: list[dict[str, Any]] | str | None = None,
    ) -> str:
        """Build a chart from a query ref (Q1) or a compute_trend series (D1).

        Pass `encoding` as an object and `params` as a list. (They're also
        accepted as JSON strings — DeepSeek sometimes serialises nested args —
        and parsed here, so a stringified arg no longer sinks the whole run.)
        """
        encoding = _as_json(encoding)
        params = _as_json(params)
        if not isinstance(encoding, dict):
            return (
                "make_chart: `encoding` must be an object mapping channels to "
                'fields, e.g. {"x": {...}, "y": {...}}.'
            )
        if params is not None and not isinstance(params, list):
            return "make_chart: `params` must be a list of selection objects, or omitted."
        spec: dict[str, Any] = {"mark": mark, "encoding": encoding}
        if title:
            spec["title"] = title
        if params:
            spec["params"] = params
        try:
            validated = validate_chart_spec(spec)
        except UnsafeChartSpecError as exc:
            ctx.deps.steps.append({"kind": "chart", "status": "rejected", "error": str(exc)})
            return f"chart rejected: {exc}"
        values = _resolve_data(ctx.deps, data_ref)
        if values is None:
            return f"unknown data_ref {data_ref!r}; use a query ref (Q1) or compute_trend ref (D1)."
        # Idempotent per data_ref: some models (DeepSeek here) re-issue make_chart
        # for a series they already charted, adding a whole round-trip each time
        # for an identical chart. One data_ref = one chart — hand back the existing
        # id instead of building it again.
        if data_ref in ctx.deps.charted_refs:
            existing = ctx.deps.charted_refs[data_ref]
            return f"chart {existing} already captured for {data_ref}; reuse it (don't re-chart)."
        # A compute_trend series carries a `layer` field (actual + N-mo avg). For
        # that overlay we enforce the house style deterministically — faint thin
        # actuals under a bold solid rolling-average, colored by entity — rather
        # than trusting the model's encoding, which is unreliable for this.
        if any(isinstance(r, dict) and "layer" in r for r in values):
            validated = {
                **validated,
                "mark": "line",
                "encoding": trend_overlay_encoding(validated.get("encoding", {}), values),
            }
        chart_id = ctx.deps.next_id("C", ctx.deps.charts)
        # Cap chart points generously: a multi-entity actual+rolling overlay is
        # ~4 lines × ~190 monthly points over a long window, so 500 truncated the
        # graph mid-series. 2000 fits several entities over 2010-now; Vega renders
        # it fine.
        ctx.deps.charts[chart_id] = {**validated, "data": {"values": values[:2000]}}
        ctx.deps.charted_refs[data_ref] = chart_id
        ctx.deps.steps.append(
            {"kind": "chart", "status": "captured", "mark": mark, "title": title, "ref": chart_id}
        )
        return f"chart {chart_id} captured"

    @agent.tool
    async def remember(ctx: RunContext[_Deps], fact: str) -> str:
        """Store a durable user preference about how they want answers."""
        await remember_memory(ctx.deps.user_id, fact)
        ctx.deps.steps.append({"kind": "memory", "status": "saved", "fact": fact})
        return "remembered"


def _as_json(value: Any) -> Any:
    """Parse a JSON string into its object/list, else pass the value through.

    DeepSeek intermittently sends a nested tool arg (make_chart's `encoding` /
    `params`) as a JSON-encoded STRING rather than a real object. pydantic-ai
    rejects that against a dict/list type before the tool runs, the retry budget
    burns on the same mistake, and the run collapses to a partial report. We
    widen those params to also accept str and coerce here so a stringified arg is
    tolerated instead of fatal.
    """
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


@lru_cache(maxsize=1)
def _catalog_columns() -> dict[str, set[str]]:
    """{'marts.mart_sales_summary': {'suburb', ...}} for lookup_values validation."""
    from .schema import get_catalog

    out: dict[str, set[str]] = {}
    for t in get_catalog(role="user"):
        rel = f"{t['schema']}.{t['table']}"
        out[rel] = {c["name"] for c in t.get("columns", [])}
    return out


def _lookup_values_sql(table: str, column: str, pattern: str) -> str | None:
    """Build a bounded DISTINCT lookup, or None if table/column aren't in the catalog.

    Table and column are validated against the queryable catalog (they're
    interpolated, not bound, so they must be allowlisted identifiers); the
    user-supplied pattern is a value, so it's single-quote-escaped and matched
    with ILIKE. run_select still enforces SELECT-only, RLS, and the timeout.
    """
    cols = _catalog_columns().get(table)
    if cols is None or column not in cols:
        return None
    needle = pattern.replace("'", "''")
    if "%" not in needle and "_" not in needle:
        needle = f"%{needle}%"
    return (
        f"SELECT DISTINCT {column} FROM {table} "
        f"WHERE {column} ILIKE '{needle}' ORDER BY {column} LIMIT 50"
    )


def _resolve_data(deps: _Deps, data_ref: str) -> list[dict[str, Any]] | None:
    if data_ref in deps.derived:
        return deps.derived[data_ref]
    q = deps.queries.get(data_ref)
    if q is None:
        return None
    cols = q["columns"]
    return [dict(zip(cols, row, strict=True)) for row in q["rows"]]


def _first_chart(charts: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    return next(iter(charts.values())) if charts else None


def _stringify(value: Any) -> str:
    """JSON-safe string for a trace payload (tool args/returns, prompts)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _tool_args(part: Any) -> Any:
    """A ToolCallPart's args, as a dict when the model sent JSON text."""
    args = getattr(part, "args", None)
    if isinstance(args, str):
        try:
            return json.loads(args)
        except (ValueError, TypeError):
            return args
    return args


def _build_trace(messages: list[Any]) -> list[dict[str, Any]]:
    """Flatten the pydantic-ai message history into an ordered, JSON-safe trace.

    One step per meaningful part, in exact chronological order — the system
    prompt, the user question, each model turn (its text/thinking, the tool calls
    it made, and that request's token usage), each tool return, and any retry
    prompts. This is the exact synchronous transcript the admin/chat trace shows,
    so nothing the agent did is hidden.
    """
    trace: list[dict[str, Any]] = []
    for msg in messages:
        parts = getattr(msg, "parts", None) or []
        if getattr(msg, "kind", None) == "response":
            texts: list[str] = []
            thinking: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for part in parts:
                pk = getattr(part, "part_kind", "")
                if pk == "text" and getattr(part, "content", None):
                    texts.append(str(part.content))
                elif pk == "thinking" and getattr(part, "content", None):
                    thinking.append(str(part.content))
                elif pk == "tool-call":
                    tool_calls.append(
                        {
                            "name": getattr(part, "tool_name", ""),
                            "args": _stringify(_tool_args(part)),
                            "tool_call_id": getattr(part, "tool_call_id", None),
                        }
                    )
            usage = getattr(msg, "usage", None)
            trace.append(
                {
                    "kind": "model",
                    "content": "\n".join(texts),
                    "thinking": "\n".join(thinking) or None,
                    "tool_calls": tool_calls,
                    "model_name": getattr(msg, "model_name", None),
                    "input_tokens": getattr(usage, "input_tokens", None) if usage else None,
                    "output_tokens": getattr(usage, "output_tokens", None) if usage else None,
                    "total_tokens": getattr(usage, "total_tokens", None) if usage else None,
                    # Prompt-cache hits: providers (DeepSeek, Anthropic) bill a
                    # cached prefix ~10x cheaper, so input_tokens overstates real
                    # cost. cache_read_tokens is the already-billed-cheap subset —
                    # recorded so a run reports EFFECTIVE, not nominal, token cost.
                    "cache_read_tokens": getattr(usage, "cache_read_tokens", None)
                    if usage
                    else None,
                    "cache_write_tokens": getattr(usage, "cache_write_tokens", None)
                    if usage
                    else None,
                }
            )
        else:  # request: system prompt, user question, tool returns, retries
            for part in parts:
                pk = getattr(part, "part_kind", "")
                if pk == "system-prompt":
                    trace.append({"kind": "system", "content": str(part.content)})
                elif pk == "user-prompt":
                    trace.append({"kind": "user", "content": _stringify(part.content)})
                elif pk == "tool-return":
                    trace.append(
                        {
                            "kind": "tool_return",
                            "name": getattr(part, "tool_name", ""),
                            "tool_call_id": getattr(part, "tool_call_id", None),
                            "content": _stringify(getattr(part, "content", "")),
                        }
                    )
                elif pk == "retry-prompt":
                    trace.append(
                        {
                            "kind": "retry",
                            "name": getattr(part, "tool_name", None),
                            "content": _stringify(getattr(part, "content", "")),
                        }
                    )
    return trace


def _fallback_draft(deps: _Deps) -> ReportDraft:
    """Minimal draft used when the model errored mid-run but real queries ran.

    Preserves the governed SQL results (and any chart the model managed to
    build) so a partial-but-real answer beats collapsing to the offline stub.
    The summary is honest about being partial: if the run stalled during
    discovery (only tiny probe results), say the main question wasn't answered
    rather than dressing up a suburb-name list as the result.
    """
    primary = select_primary_query(deps.queries)
    answered = bool(primary and primary.get("row_count", 0) > 1)
    summary = (
        "Here are the results I gathered for your question. I couldn't finish the "
        "full written analysis, but the underlying data — and any chart — are shown below."
        if answered
        else (
            "I wasn't able to finish answering this one — the run stopped before the "
            "main query completed. Any data I did gather is shown below; please try "
            "rephrasing or narrowing the question."
        )
    )
    return ReportDraft(summary=summary, main_chart_ref=next(iter(deps.charts), None))
