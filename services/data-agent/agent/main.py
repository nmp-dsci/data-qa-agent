from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import logfire
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .otlp import otlp_processors

# Configured before importing sandbox_agent: agent_common (pulled in by that
# module) instruments pydantic-ai/httpx at import time, which needs
# logfire.configure() to have already run.
logfire.configure(
    service_name="data-agent",
    send_to_logfire="if-token-present",
    # s37: also export to a self-hosted collector when OTLP_ENDPOINT is set.
    additional_span_processors=otlp_processors(),
)

# M5 (architecture tab): only sdk_agent's own tool-metadata constants are read
# here (TOOL_DESCRIPTIONS/TOOL_SCHEMAS/GOVERNED_TOOLS/BUILTIN_TOOLS/MCP_SERVER).
# Importing the module is safe with no `claude_agent_sdk` installed — that
# import is lazy inside sdk_agent._load_sdk(), called only when a run actually
# drives the SDK — so this module-level import never breaks the offline stub
# or the pydantic_ai champion path.
from . import analytics, sdk_agent  # noqa: E402
from .chart import trend_overlay_encoding, validate_chart_spec  # noqa: E402
from .config import settings  # noqa: E402
from .db import admin_engine, engine, load_database_catalog, run_select  # noqa: E402
from .deck import DEFAULT_CATALOGUE, render_layouts_md  # noqa: E402
from .eval_graders import (  # noqa: E402
    grade_artifact,
    grade_extraction,
    grade_presentation_format,
)
from .eval_judge import judge_insight  # noqa: E402
from .knowledge import knowledge_version, load_pages, read_knowledge  # noqa: E402
from .nl2sql import build_sql, phrase_answer  # noqa: E402
from .pages import chart_object_from_spec, compose_pages, page_plan, planned_kinds  # noqa: E402
from .provider import choose_provider  # noqa: E402
from .sandbox import explain_sandbox_error, run_code  # noqa: E402
from .sandbox.extract import extract  # noqa: E402
from .sandbox_agent import answer_with_sandbox  # noqa: E402
from .schema import (  # noqa: E402
    USER_VISIBLE_SCHEMAS,
    describe_table,
    get_catalog,
    list_marts,
    merge_catalogs,
)
from .sql_assist import sql_assist  # noqa: E402
from .sql_guardrails import UnsafeSQLError  # noqa: E402
from .titles import summarize_title  # noqa: E402
from .version import build_fingerprint  # noqa: E402


async def _warmup() -> None:
    """s40 M0: pre-load the embedding model and spawn one sandbox run so the
    first real request doesn't pay the cold-start tax (ONNX load + first
    Node/pyodide spawn). Best-effort — a failure degrades to a slow first
    request, exactly the behaviour we have today.
    """
    from .embeddings import embed_text

    try:
        await asyncio.to_thread(embed_text, "warmup")
    except Exception as exc:  # noqa: BLE001 — warm-up must never block startup
        print(f"[data-agent] embedding warmup skipped: {exc}")
    try:
        await asyncio.to_thread(run_code, "result = {'answer': 'warmup', 'metrics': []}")
    except Exception as exc:  # noqa: BLE001
        print(f"[data-agent] sandbox warmup skipped: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if settings.warmup_on_start:
        await _warmup()
    yield
    await engine.dispose()
    await admin_engine.dispose()


app = FastAPI(title="data-qa-agent :: data-agent", version="0.1.0", lifespan=lifespan)
logfire.instrument_fastapi(app)


@app.middleware("http")
async def _require_shared_token(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Reject callers without the shared token when one is configured (s12).

    The cloud agent sits on a public App Runner URL with the backend as its only
    intended caller. /health stays open for the platform health checker.
    """
    token = settings.agent_shared_token
    if token and request.url.path != "/health":
        supplied = request.headers.get("x-agent-token", "")
        if not secrets.compare_digest(supplied.encode(), token.encode()):
            return JSONResponse(status_code=401, content={"detail": "invalid X-Agent-Token"})
    return await call_next(request)


class UserCtx(BaseModel):
    id: str
    role: str = "user"
    # App plan (s10): gates how many answer pages this user gets (free|plus|pro).
    # Missing/unknown values fall back to "free" — the cheapest, least-revealing
    # behaviour (page_plan treats any unrecognised value as free).
    plan: str = "free"


class AskRequest(BaseModel):
    question: str
    user: UserCtx
    dataset_slug: str = "nsw_sales"


class AgentAnswer(BaseModel):
    answer: str
    sql: str | None = None
    columns: list[str] = []
    rows: list[list[Any]] = []
    row_count: int = 0
    chart: dict[str, Any] | None = None
    engine: str = "stub"
    input_tokens: int | None = None
    output_tokens: int | None = None
    # Prompt-cache split + priced cost (s32 W2). The agent computes the cost
    # because only it knows which provider/model actually answered; the backend
    # persists what it is given. Nominal input_tokens is ~6x real spend on this
    # workload, so the split is what makes the deck's cost tile correct.
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    cost_usd: float | None = None
    # Reliability (s32 W1): the answer came back, but not the one that was asked
    # for — a retried-then-stubbed run, or a provider outage the user rode out.
    # Persisted as query_runs.status = 'degraded', which is the deck's
    # degraded-rate tile and half of SLO-A.
    degraded: bool = False
    attempts: int = 1
    # Why the answer isn't a real answer, when it isn't (s32 W3). Until now a
    # guard-rejected question came back as a polite sentence and the backend
    # stamped the run 'success' — so the one signal that matters most for a
    # security review was the one the audit trail could not show. Set here,
    # persisted as query_runs.status/error.
    error: str | None = None
    # A guard or policy REFUSED this request (as opposed to it failing). Counted
    # as the deck's denial signal, separately from errors.
    denied: bool = False
    # Ordered step-by-step trace (each SQL attempt/chart/memory) for admin inspection.
    steps: list[dict[str, Any]] = []
    # Structured InsightReport (K2) — present on the LLM path; None for the stub.
    report: dict[str, Any] | None = None
    # Pages contract (s07): Summary → Insights pages of governed objects
    # (data + intent) the frontend's template registry renders with visx.
    pages: list[dict[str, Any]] | None = None
    # s46: the Sheets/Slides deck this run produced — deck/embed/sheet URLs plus
    # the per-slide manifest (which layout was picked and every option passed).
    # None when deck export is off, which is every deployment but dev.
    #
    # This field being absent is what made three end-to-end runs report "no
    # artifact" while the deck was in fact built correctly every time: the
    # runtime returned it, and FastAPI silently dropped it serialising through
    # this model. A response model omission is invisible at both ends — the
    # producer sees success, the consumer sees a missing feature.
    artifact: dict[str, Any] | None = None


class SqlRequest(BaseModel):
    sql: str
    user: UserCtx


class SqlResult(BaseModel):
    columns: list[str] = []
    rows: list[list[Any]] = []
    row_count: int = 0
    truncated: bool = False
    sql: str | None = None
    error: str | None = None
    # s32 W3: the guard REFUSED this SQL, as opposed to the database failing to
    # run it. Flagged here rather than inferred from the error text downstream,
    # so the deck's denial counter can't drift with a reworded message.
    denied: bool = False


class ConfigItem(BaseModel):
    key: str  # the env var / setting name
    value: str  # display value (secrets shown as "set"/"not set", never the value)
    note: str | None = None  # short human hint (allowed values, what the limit guards)
    secret: bool = False


class ConfigSection(BaseModel):
    title: str
    service: str
    items: list[ConfigItem]


def _redact_db_url(url: str) -> str:
    """Strip credentials from a SQLAlchemy URL: keep driver/host/db, hide user:pw."""
    try:
        scheme, rest = url.split("://", 1)
    except ValueError:
        return "***"
    if "@" in rest:
        rest = rest.split("@", 1)[1]
    return f"{scheme}://***@{rest}"


def _secret_item(key: str, value: str | None, note: str | None = None) -> ConfigItem:
    return ConfigItem(key=key, value="set" if value else "not set", note=note, secret=True)


@app.get("/health")
async def health() -> dict[str, str]:
    selected = choose_provider(
        settings.llm_provider, settings.deepseek_api_key, settings.anthropic_api_key
    )
    return {"status": "ok", "provider": selected[0] if selected else "stub"}


def _money(value: Any) -> str:
    try:
        return f"${float(value):,.0f}"
    except (TypeError, ValueError):
        return str(value)


def _pct(value: Any) -> str:
    try:
        return f"{float(value):+.1f}%"
    except (TypeError, ValueError):
        return str(value)


def _sales_trend_stub_report(result: dict[str, Any]) -> dict[str, Any] | None:
    """Build a report-shaped fallback for the deterministic sales_trend stub.

    The sandbox LLM path can fail before producing a report. For this high-value
    trend intent, keep the UI experience consistent by deriving the chart and
    headline maths from the same governed rows the stub already returned.
    """
    columns = result.get("columns", [])
    rows = result.get("rows", [])
    if not rows:
        return None

    try:
        grouped = analytics.build_series(
            columns,
            rows,
            month_col="month",
            value_col="avg_sale_price",
            count_col="n_sold",
            group_col="suburb",
        )
    except KeyError:
        return None

    chart_values: list[dict[str, Any]] = []
    latest_by_suburb: dict[str, dict[str, Any] | None] = {}
    growth_5y: dict[str, float | None] = {}
    growth_3y: dict[str, float | None] = {}
    for suburb, series in grouped.items():
        latest_by_suburb[suburb] = analytics.latest_reliable(series, smooth_window=6)
        growth_5y[suburb] = analytics.growth_rate(series, years=5)
        growth_3y[suburb] = analytics.growth_rate(series, years=3)
        for point in analytics.chart_series(series, rolling_window=6):
            chart_values.append(
                {
                    "month": f"{point['month']}-01",
                    "value": point["value"],
                    "series": suburb,
                    "layer": point["layer"],
                }
            )

    suburbs = sorted(grouped)
    latest_parts = []
    for suburb in suburbs:
        latest = latest_by_suburb.get(suburb)
        if latest:
            latest_parts.append(f"{suburb} {_money(latest['value'])} ({latest['month']})")
    growth_parts = [
        f"{suburb} {_pct(growth)}"
        for suburb, growth in sorted(growth_5y.items())
        if growth is not None
    ]
    summary = (
        f"House sale-price trend for {', '.join(suburbs)} from "
        f"{min(p['month'] for s in grouped.values() for p in s)} to "
        f"{max(p['month'] for s in grouped.values() for p in s)}."
    )
    if growth_parts:
        summary += " Five-year growth: " + "; ".join(growth_parts) + "."

    chart = validate_chart_spec(
        {
            "mark": "line",
            "title": "House sale-price trend",
            "encoding": trend_overlay_encoding(
                {
                    "x": {"field": "month", "type": "temporal", "title": None},
                    "y": {
                        "field": "value",
                        "type": "quantitative",
                        "title": "Average sale price",
                        "axis": {"format": "$,.0f"},
                    },
                    "tooltip": [
                        {"field": "series", "type": "nominal", "title": "Suburb"},
                        {"field": "layer", "type": "nominal", "title": "Series"},
                        {"field": "month", "type": "temporal", "title": "Month"},
                        {
                            "field": "value",
                            "type": "quantitative",
                            "title": "Average price",
                            "format": "$,.0f",
                        },
                    ],
                },
                chart_values,
            ),
        }
    )
    chart = {**chart, "data": {"values": chart_values[:2000]}}

    headlines = [
        {
            "element_id": f"headline:{idx}",
            "label": suburb,
            "value": _money(latest["value"]) if latest else "n/a",
            "basis": f"latest 6-month average, {latest['month']}" if latest else "",
            "related": False,
            "query_ref": "Q1",
        }
        for idx, (suburb, latest) in enumerate(sorted(latest_by_suburb.items()))
    ]
    insights = [
        {
            "element_id": "insight:0",
            "heading": "The chart uses both raw monthly values and a 6-month average",
            "body": (
                "Thin monthly sales can make the raw line jump. The bold 6-month "
                "average gives the trend used for the headline growth figures."
            ),
            "query_refs": ["Q1"],
            "chart": None,
        }
    ]
    if latest_parts:
        insights.append(
            {
                "element_id": "insight:1",
                "heading": "Latest smoothed values",
                "body": "; ".join(latest_parts) + ".",
                "query_refs": ["Q1"],
                "chart": None,
            }
        )
    if growth_3y:
        parts = [
            f"{suburb} {_pct(growth)}"
            for suburb, growth in sorted(growth_3y.items())
            if growth is not None
        ]
        if parts:
            insights.append(
                {
                    "element_id": "insight:2",
                    "heading": "Recent three-year growth",
                    "body": "; ".join(parts) + ".",
                    "query_refs": ["Q1"],
                    "chart": None,
                }
            )

    report = {
        "element_id": "report",
        "summary": summary,
        "headlines": headlines,
        "insights": insights,
        "profiles": [],
        "main_chart": chart,
        "queries": [
            {
                "element_id": "query:Q1",
                "ref": "Q1",
                "purpose": "monthly house sale-price trend by suburb",
                "sql": result.get("sql"),
                "columns": columns,
                "rows": rows,
                "row_count": result.get("row_count", 0),
            }
        ],
        "knowledge_pages_used": [],
        "knowledge_version": knowledge_version(),
        "fallback_reason": "sandbox_llm_did_not_complete_report",
    }
    return {"answer": summary, "chart": chart, "report": report}


class GradeRequest(BaseModel):
    """One case's golden vs one agent answer (s24 M2)."""

    question: str
    grader: dict[str, Any] = {}
    golden_rows: list[dict[str, Any]] = []
    actual_rows: list[dict[str, Any]] = []
    report: dict[str, Any] | None = None
    answer: str = ""
    # s46: the Slides/Sheets manifest this run produced, when deck export is on.
    # None on a run without it, which is a normal state, not a failure.
    artifact: dict[str, Any] | None = None
    # Set false to score G1/G2/G3-structural only and skip the LLM call.
    judge: bool = True


@app.post("/agent/eval/grade")
async def eval_grade(req: GradeRequest) -> dict[str, Any]:
    """Score one answer against its golden.

    Lives in the data-agent because that is where the graders, the report
    linter, and LLM access already are — the runner stays a thin orchestrator
    and there is exactly one implementation of each grader.
    """
    spec = req.grader or {}
    kind = str(spec.get("kind") or "")

    # G1 — extraction: are the numbers right?
    if kind:
        g1 = grade_extraction(
            kind=kind,
            golden_rows=req.golden_rows,
            actual_rows=req.actual_rows,
            key=str(spec.get("key") or ""),
            value=str(spec.get("value") or ""),
            k=int(spec.get("k") or 5),
            tolerance_pct=float(spec.get("tolerance_pct") or 1.0),
        )
    else:
        # No declared kind: say so rather than guessing a shape and reporting a
        # number the golden's author never asked for.
        g1 = {"kind": "", "score": None, "error": "golden has no grader.kind"}

    # G3 (deterministic half) — is the delivered report well-formed?
    g3_format = grade_presentation_format(
        req.report, expected_objects=list(spec.get("expected_objects") or [])
    )

    # G3 (insight half) — is the answer worth reading?
    g3_insight = (
        await judge_insight(question=req.question, answer=req.answer)
        if req.judge
        else {"skipped": True, "reason": "judge disabled for this run"}
    )

    # G5 — the artifact the user actually received (s46). Only scored when the
    # run produced one: a deployment without deck export is not a failing run,
    # it is a differently-configured one, so this stays None rather than 0.
    g5_artifact = (
        grade_artifact(
            req.artifact,
            expect_chart=bool(spec.get("expect_chart", True)),
            min_slides=int(spec.get("min_slides", 1)),
        )
        if req.artifact
        else None
    )

    return {
        "g1": g1,
        "g3_format": g3_format,
        "g3_insight": g3_insight,
        "g5_artifact": g5_artifact,
    }


class JudgeRequest(BaseModel):
    """One live answer to score for insight quality, with no golden (s32 W4)."""

    question: str
    answer: str
    evidence: str = ""


@app.post("/agent/eval/judge")
async def eval_judge_only(req: JudgeRequest) -> dict[str, Any]:
    """Score insight quality alone — the online sampler's entry point.

    Separate from ``/agent/eval/grade`` because the inputs genuinely differ: that
    endpoint compares an answer to a golden and returns G1/G2/G3; this one has no
    golden at all, so it can only score whether the answer is *worth reading* —
    grounded, direct, explains why, so-what, clear. It cannot tell you the numbers
    are right. Folding it into the grade endpoint would let a caller ask for G1
    with no ground truth and get a confident-looking null.

    Same frozen, hashed rubric as the eval judge, and the same refusal to grade a
    model of its own family — so a run with no cross-family key records a
    ``skipped`` verdict rather than a fabricated score.
    """
    return await judge_insight(question=req.question, answer=req.answer, evidence=req.evidence)


@app.get("/agent/version")
async def agent_version() -> dict[str, str]:
    """The composed build fingerprint of this agent (s24 M1).

    The backend upserts this into ``app.agent_versions`` and stamps the
    resulting id onto every ``app.query_runs`` row, so any answer — and any eval
    score derived from it — is attributable to an exact build.
    """
    return build_fingerprint()


@app.get("/agent/config", response_model=ConfigSection)
async def agent_config() -> ConfigSection:
    """Resolved data-agent config for the admin panel. Secrets are redacted."""
    s = settings
    active_model = s.deepseek_model if s.llm_provider == "deepseek" else s.model
    items = [
        ConfigItem(
            key="SANDBOX_RUNTIME",
            value=s.sandbox_runtime,
            note="pyodide (WASM, hardened) | subprocess",
        ),
        ConfigItem(key="APP_ENV", value=s.app_env),
        ConfigItem(
            key="AGENT_RUNTIME",
            value=s.agent_runtime,
            note="pydantic_ai (champion) | agent_sdk (Claude Agent SDK challenger)",
        ),
        ConfigItem(key="LLM_PROVIDER", value=s.llm_provider, note="deepseek | anthropic"),
        ConfigItem(key="model", value=active_model, note="model used by the active provider"),
        _secret_item("DEEPSEEK_API_KEY", s.deepseek_api_key, note="empty = offline stub"),
        _secret_item("ANTHROPIC_API_KEY", s.anthropic_api_key, note="empty = offline stub"),
        ConfigItem(key="MAX_ROWS", value=str(s.max_rows), note="row cap for one result set"),
        ConfigItem(
            key="MAX_SQL_ATTEMPTS", value=str(s.max_sql_attempts), note="run_sql attempt budget"
        ),
        ConfigItem(
            key="SQL_STATEMENT_TIMEOUT_MS",
            value=str(s.sql_statement_timeout_ms),
            note="hard per-statement timeout",
        ),
        ConfigItem(
            key="SANDBOX_RUN_ATTEMPTS",
            value=str(s.sandbox_run_attempts),
            note="sandbox mode: run_analysis attempts",
        ),
        ConfigItem(
            key="AGENT_REQUEST_LIMIT",
            value=str(s.agent_request_limit),
            note="primary runaway guard (requests/run)",
        ),
        ConfigItem(
            key="AGENT_TOTAL_TOKENS_LIMIT",
            value=str(s.agent_total_tokens_limit),
            note="nominal token ceiling per run (~6x cache-inflated)",
        ),
        ConfigItem(
            key="MAX_KNOWLEDGE_READS",
            value=str(s.max_knowledge_reads),
            note="knowledge pages loadable per run",
        ),
        ConfigItem(key="EMBEDDING_MODEL", value=s.embedding_model, note="local, no API key"),
        ConfigItem(key="DB_SSL", value=s.db_ssl or "(none)"),
        ConfigItem(
            key="AGENT_DATABASE_URL",
            value=_redact_db_url(s.agent_database_url),
            note="agent + users: read-only, RLS enforced",
        ),
        ConfigItem(
            key="ADMIN_RO_DATABASE_URL",
            value=_redact_db_url(s.admin_ro_database_url),
            note="admin SQL editor: read-only, BYPASSRLS, all schemas",
        ),
        _secret_item("LOGFIRE_TOKEN", s.logfire_token, note="ships traces to Logfire Cloud"),
    ]
    return ConfigSection(title="Data agent", service="data-agent", items=items)


def _salvage_usage(salvage: dict[str, Any] | None) -> dict[str, Any]:
    """Carry a failed LLM run's spend onto the stub answer that replaces it.

    Tokens burned before a failure are still billed, so dropping them here would
    make the deck's cost tile understate spend exactly when things go wrong —
    the moment you most want the number to be honest.
    """
    keys = (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "cost_usd",
        "attempts",
    )
    if salvage is None:
        return {}
    return {k: salvage[k] for k in keys if salvage.get(k) is not None}


async def _pace_stub_frames(progress: asyncio.Queue[dict[str, Any]] | None) -> None:
    """s40 (D2): spread the forced stub across STUB_LATENCY_S with progress
    frames trickling out, so pages and the result land at ~S seconds the way a
    real LLM answer would. The relay path, TTFP, and queue-wait measurements
    then exercise the same shape in stub and live runs.
    """
    total = max(0.0, settings.stub_latency_s)
    if total <= 0:
        return
    slices = 5
    for i in range(slices):
        await asyncio.sleep(total / slices)
        if progress is not None:
            progress.put_nowait(
                {"n": i + 1, "action": "Working (stub)", "detail": f"{i + 1}/{slices}"}
            )


async def _run_agent(
    body: AskRequest,
    *,
    user_id: str,
    progress: asyncio.Queue[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Dispatch one question to the configured agent runtime (agent_sdk M1).

    The single seam between champion and challenger. Both take the same
    arguments and return the same contract (answer dict / no_answer report /
    salvage dict / None), so everything downstream — SSE relay, persistence,
    the queue worker, which reaches this through ``_answer`` — is untouched by
    the choice. LLM_STUB is checked by the caller and still wins over both.
    """
    if settings.agent_runtime == "agent_sdk":
        from .sdk_agent import answer_with_sdk

        return await answer_with_sdk(
            body.question, user_id=user_id, plan=body.user.plan, progress=progress
        )
    return await answer_with_sandbox(
        body.question, user_id=user_id, plan=body.user.plan, progress=progress
    )


async def _answer(
    body: AskRequest, progress: asyncio.Queue[dict[str, Any]] | None = None
) -> AgentAnswer:
    """Produce an answer for one question (shared by /agent/ask and its stream).

    When ``progress`` is supplied, the sandbox agent pushes live step events onto
    it as it works; the caller drains and forwards them as SSE frames.
    """
    user_id = body.user.id

    # Preferred path: the sandbox agent on the configured LLM provider. Returns
    # None (→ deterministic offline stub below) when no provider key is set; a
    # salvage dict (fallback=True) when the LLM ran but never completed a report
    # — its trace (model turns, tool calls, tokens) stays with the stub answer.
    # s40 (D2): LLM_STUB=1 skips the provider entirely — the stub below answers,
    # paced by _pace_stub_frames so the run keeps a real run's timing shape.
    llm = None
    if not settings.llm_stub:
        llm = await _run_agent(body, user_id=user_id, progress=progress)
    salvage: dict[str, Any] | None = None
    if llm is not None:
        if not llm.get("fallback"):
            return AgentAnswer(**llm)
        salvage = llm

    salvaged_steps: list[dict[str, Any]] = list(salvage["steps"]) if salvage else []

    # Streaming pages on the stub path (s10): the offline stub never emitted a
    # plan frame (no provider), so emit one now; the salvage path already did.
    plan_slots = page_plan(plan=body.user.plan)
    page_index = {s["kind"]: s["index"] for s in plan_slots if s["status"] != "locked"}
    if progress is not None and llm is None:
        progress.put_nowait({"event": "plan", "pages": plan_slots})
    if settings.llm_stub:
        await _pace_stub_frames(progress)

    def _emit_stub_pages(pages: list[dict[str, Any]]) -> None:
        """Emit page frames for the stub's pages, then skip the rest (clears ghosts)."""
        if progress is None:
            return
        emitted: set[str] = set()
        for p in pages:
            kind = p.get("kind", p["template"])
            index = page_index.get(kind)
            if index is None:
                continue
            emitted.add(kind)
            progress.put_nowait(
                {
                    "event": "page",
                    "index": index,
                    "kind": kind,
                    "status": "complete",
                    "page": p,
                }
            )
        for kind, index in page_index.items():
            if kind not in emitted:
                progress.put_nowait(
                    {"event": "page", "index": index, "kind": kind, "status": "skipped"}
                )

    sql, intent = build_sql(body.question)
    try:
        result = await run_select(sql, user_id=user_id)
    except UnsafeSQLError as exc:
        _emit_stub_pages([])
        # s32 W3: a refusal is recorded AS a refusal. The user still gets the
        # plain-English sentence, but the run carries error + denied so the audit
        # trail and the deck's denial counter both see it.
        return AgentAnswer(
            answer=f"I couldn't run that safely: {exc}",
            sql=sql,
            error=f"guard rejected generated SQL: {exc}",
            denied=True,
            **_salvage_usage(salvage),
            steps=[
                *salvaged_steps,
                {"kind": "sql", "attempt": 1, "sql": sql, "status": "error", "error": str(exc)},
            ],
        )

    answer = phrase_answer(body.question, intent, result)
    fallback_report = _sales_trend_stub_report(result) if intent == "sales_trend" else None
    if fallback_report is not None:
        answer = fallback_report["answer"]
    steps: list[dict[str, Any]] = [
        *salvaged_steps,
        {
            "kind": "sql",
            "attempt": 1,
            "sql": result["sql"],
            "status": "success",
            "row_count": result["row_count"],
            "intent": intent,
        },
    ]
    pages: list[dict[str, Any]] | None = None
    report = fallback_report["report"] if fallback_report else None
    if report is not None:
        pages, page_steps = compose_pages(report, question=body.question)
        # The stub honours the user's plan too (s10).
        allowed = set(planned_kinds(body.user.plan))
        pages = [p for p in pages if p.get("kind", p["template"]) in allowed]
        steps.extend(page_steps)
        if pages:
            report["pages"] = pages
    _emit_stub_pages(pages or [])
    return AgentAnswer(
        answer=answer,
        sql=result["sql"],
        columns=result["columns"],
        rows=result["rows"],
        row_count=result["row_count"],
        chart=fallback_report["chart"] if fallback_report else None,
        engine="stub",
        report=report,
        pages=pages or None,
        steps=steps,
        # A salvage means the LLM path ran and failed, so this answer is the
        # stub standing in for it — degraded (s32 W1). A stub answer with no
        # salvage is the configured offline behaviour, not a degradation.
        degraded=salvage is not None,
        **_salvage_usage(salvage),
    )


@app.post("/agent/ask", response_model=AgentAnswer)
async def agent_ask(body: AskRequest) -> AgentAnswer:
    return await _answer(body)


def _sse(event: str, data: dict[str, Any] | str) -> str:
    payload = data if isinstance(data, str) else json.dumps(data)
    return f"event: {event}\ndata: {payload}\n\n"


@app.post("/agent/ask/stream")
async def agent_ask_stream(body: AskRequest) -> StreamingResponse:
    """SSE variant of /agent/ask: forwards the sandbox agent's live step events
    (``progress`` frames) and the s10 page stream (one ``plan`` frame, then a
    ``page`` frame per finished page) as they happen, then one ``result`` frame
    with the full AgentAnswer. A ``status`` heartbeat every 2s keeps the
    connection warm while a single step runs. Same answer/persistence contract
    as /agent/ask."""

    async def gen() -> AsyncIterator[str]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        task = asyncio.ensure_future(_answer(body, progress=queue))
        while not task.done() or not queue.empty():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=2.0)
            except TimeoutError:
                yield _sse("status", {"state": "working"})
                continue
            # Queue items carrying an "event" key are typed frames (plan/page);
            # everything else is a legacy {n, action, detail} progress step.
            name = event.pop("event", None)
            yield _sse(name if name in ("plan", "page") else "progress", event)
        try:
            result = task.result()
            yield _sse("result", result.model_dump_json())
        except Exception as exc:  # noqa: BLE001 — surface the failure to the stream
            yield _sse("error", {"detail": str(exc)})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/agent/sql", response_model=SqlResult)
async def agent_sql(body: SqlRequest) -> SqlResult:
    """Run raw user SQL through the SAME governed executor the agent uses.

    run_select enforces every guardrail: validate_select (SELECT-only, single
    statement), a read-only role, a statement timeout, and the row cap. Admins
    run as admin_ro (BYPASSRLS, SELECT on every schema) so the SQL editor can
    inspect any table incl. internal app.* ones; everyone else runs as agent_ro
    under their RLS context (marts + staging, their own rows). Read-only either way.
    """
    try:
        result = await run_select(
            body.sql, user_id=body.user.id, as_admin=(body.user.role == "admin")
        )
    except UnsafeSQLError as exc:
        return SqlResult(sql=body.sql, error=str(exc), denied=True)
    except Exception as exc:  # noqa: BLE001 — surface DB errors (syntax, timeout) to the editor
        return SqlResult(sql=body.sql, error=str(exc))
    return SqlResult(
        columns=result["columns"],
        rows=result["rows"],
        row_count=result["row_count"],
        truncated=result["row_count"] >= settings.max_rows,
        sql=result["sql"],
    )


class SqlAssistRequest(BaseModel):
    action: str = "generate"  # generate | explain | fix | optimize
    prompt: str | None = None
    sql: str | None = None
    user: UserCtx


class SqlAssistResult(BaseModel):
    sql: str | None = None
    explanation: str | None = None
    engine: str = "stub"
    error: str | None = None


@app.post("/agent/sql/assist", response_model=SqlAssistResult)
async def agent_sql_assist(body: SqlAssistRequest) -> SqlAssistResult:
    """AI assist for the SQL editor (Phase C): generate/explain/fix/optimize.

    Only authors or edits SQL text — it never executes anything. Whatever SQL it
    returns is run (if at all) through the same governed /agent/sql executor, so
    the read-only role, RLS, and guardrails still apply.
    """
    result = await sql_assist(
        action=body.action,
        prompt=body.prompt,
        sql=body.sql,
        user_id=body.user.id,
    )
    return SqlAssistResult(**result)


class TitleRequest(BaseModel):
    question: str


class TitleResponse(BaseModel):
    title: str


@app.post("/agent/title", response_model=TitleResponse)
async def agent_title(body: TitleRequest) -> TitleResponse:
    """A 3-5 word conversation title for a question (s17 E1).

    Isolated from the answer path: the backend calls this best-effort on the first
    answer and a backfill script reuses it. Falls back to an offline heuristic when
    no LLM provider is configured, so it can never fail a chat.
    """
    return TitleResponse(title=await summarize_title(body.question))


class AnalysisRequest(BaseModel):
    sql: str
    code: str = ""
    # s18 Golden Sandbox: named presentation objects to (re)compute against the
    # SAME extract — each ``{element_id, object_type, code}`` — so the builder can
    # repopulate every built object on golden load in one round-trip.
    objects: list[dict[str, Any]] = []
    user: UserCtx


class AnalysisResponse(BaseModel):
    columns: list[str] = []
    rows: list[list[Any]] = []
    row_count: int = 0
    report: dict[str, Any] | None = None
    pages: list[dict[str, Any]] | None = None
    # The enrichment stage: named derived frames the run built + fed to a skill,
    # so the Golden builder can show extract → derived frames → objects.
    frames: list[dict[str, Any]] = []
    skills_used: list[str] = []
    skill_gaps: list[dict[str, Any]] = []
    # s18: each named object recomputed against the extract — {element_id, object, error}.
    objects_out: list[dict[str, Any]] = []
    error: str | None = None


def _lift_object(
    report: dict[str, Any] | None,
    *,
    element_id: str,
    object_type: str = "compare",
    sql: str | None = None,
    dataset: str | None = None,
) -> dict[str, Any] | None:
    """Lift a built object's report into ONE page object with a stable element_id.

    Charts lift their ``main_chart`` (combo-aware, via chart_object_from_spec);
    a ``table`` object lifts the report's ``table`` payload (from
    ``skills.data_table``) verbatim; kpi/headline objects carry no chart, so the
    first headline tile is lifted. ``sql`` — the governed extract behind the
    object — rides along in ``data.sql`` so golden charts get the same "open in
    SQL editor" action as chat/Explore.

    ``pivot`` is a BUILD recipe, not a render type: it shapes a cross-tab and
    emits the same ``table`` payload, so it lifts through the table branch and
    the rendered page object is a plain ``table``. That is what keeps the render
    registry (and its three-way sync test) out of this feature entirely."""
    if not isinstance(report, dict):
        return None
    table = report.get("table")
    if object_type in ("table", "pivot") and isinstance(table, dict) and table.get("columns"):
        data = {k: v for k, v in table.items() if v is not None}
        if sql:
            data["sql"] = sql
        return {"type": "table", "element_id": element_id, "role": "table", "data": data}
    spec = report.get("main_chart")
    if spec:
        lifted = chart_object_from_spec(
            spec, element_id=element_id, role="chart", height="md", sql=sql, dataset=dataset
        )
        if lifted is not None:
            return lifted.model_dump(exclude_none=True)
    heads = report.get("headlines") or []
    if heads and isinstance(heads[0], dict):
        h = heads[0]
        return {
            "type": "kpi",
            "element_id": element_id,
            "role": "headline",
            "data": {
                "label": h.get("label", ""),
                "value": h.get("value", ""),
                "basis": h.get("basis", ""),
            },
        }
    return None


def _run_named_objects(
    objects: list[dict[str, Any]], frame: Any, *, sql: str | None = None
) -> list[dict[str, Any]]:
    """Run each named object's run_analysis snippet against the extract + lift it.

    ``sql`` is the shared golden extract these objects recompute against — it rides
    into each lifted chart so its "open in SQL editor" opens the governing query."""
    out: list[dict[str, Any]] = []
    for spec in objects or []:
        if not isinstance(spec, dict):
            continue
        code = str(spec.get("code") or "")
        eid = str(spec.get("element_id") or "")
        otype = str(spec.get("object_type") or "compare")
        if not code or not eid:
            continue
        try:
            outcome = run_code(code, df=frame, frames={"extract": frame})
            obj = _lift_object(outcome.report, element_id=eid, object_type=otype, sql=sql)
            err = None if obj else explain_sandbox_error(outcome.error)
            out.append({"element_id": eid, "object": obj, "error": err})
        except Exception as exc:  # noqa: BLE001 — one bad object must not fail the prep
            out.append({"element_id": eid, "object": None, "error": str(exc)})
    return out


@app.post("/agent/analysis", response_model=AnalysisResponse)
async def agent_analysis(body: AnalysisRequest) -> AnalysisResponse:
    """Golden authoring (s14 E1) — run a confirmed SQL extract, then optionally the
    run_analysis script, in the SAME governed extract + sandbox path the agent uses.

    With no ``code`` this is the Builder's Goal A step (run the SQL, inspect rows).
    With ``code`` it is Goal B: the metrics come from the tested skills in the
    locked-down sandbox, not hand-typing — so the golden reflects a real run.
    """
    from .ordinals import load_overrides

    await load_overrides()  # s23: honour curator ordinal edits before the lift
    try:
        frame, meta = await extract(body.sql, user_id=body.user.id)
    except UnsafeSQLError as exc:
        return AnalysisResponse(error=f"extract rejected: {exc}")
    except Exception as exc:  # noqa: BLE001 — surface DB/extract errors to the builder
        return AnalysisResponse(error=f"extract failed: {exc}")

    columns = meta.get("columns", [])
    rows = meta.get("rows", [])
    row_count = meta.get("row_count", len(rows))
    # Named presentation objects recompute against this same extract (s18).
    objects_out = await asyncio.to_thread(_run_named_objects, body.objects, frame, sql=body.sql)
    if not body.code.strip():
        return AnalysisResponse(
            columns=columns, rows=rows, row_count=row_count, objects_out=objects_out
        )

    outcome = await asyncio.to_thread(run_code, body.code, df=frame, frames={"extract": frame})
    # Compose renderable pages from the produced report so the Builder can add
    # this sandbox run's output as a report page (the same PageLayout as chat).
    pages: list[dict[str, Any]] = []
    if outcome.report:
        try:
            pages, _ = compose_pages(outcome.report)
        except Exception:  # noqa: BLE001 — page composition is best-effort here
            pages = []
    # Sandbox reports carry no `queries`, so compose_pages can't attach the source
    # SQL. Stamp the shared extract onto each chart so preview charts link to it.
    for p in pages:
        for col in p.get("columns", []):
            for o in col:
                _with_sql(o if isinstance(o, dict) else None, body.sql)
    return AnalysisResponse(
        columns=columns,
        rows=rows,
        row_count=row_count,
        report=outcome.report,
        pages=pages or None,
        frames=outcome.frames,
        skills_used=outcome.skills_used,
        skill_gaps=[g.model_dump() for g in outcome.skill_gaps],
        objects_out=objects_out,
        error=explain_sandbox_error(outcome.error),
    )


_CHART_TYPES = {"trend", "breakdown", "compare", "table"}


def _with_sql(obj: dict[str, Any] | None, sql: str | None) -> dict[str, Any] | None:
    """Fill ``data.sql`` on a chart object so it links to the governing query.
    Only sets it when missing (never clobbers a query the object already knows)."""
    if obj and sql and obj.get("type") in _CHART_TYPES:
        data = obj.get("data")
        if isinstance(data, dict) and not data.get("sql"):
            data["sql"] = sql
    return obj


@app.get("/agent/skills")
async def agent_skills() -> dict[str, Any]:
    """The sandbox skill catalog (s14 Golden Examples). Lists the analysis/chart/
    report skills a run_analysis script can call as ``skills.<name>`` — name,
    group, one-line doc, and signature — so the Builder can show what's available
    and which a run used, instead of the hard-to-read plan text."""
    import inspect

    from . import skills as skill_lib

    mechanics = {"skill_gap", "note_inline_math", "reset", "used", "gaps"}
    groups = {
        "analysis": {
            "trend_series",
            "rolling_average",
            "growth_rate",
            "latest_value",
            "top_growth",
            "gross_yield",
            "driver_analysis",
        },
        "chart": {
            "trend_chart",
            "comparison_chart",
            "dual_axis_chart",
            "distribution_chart",
            "profile_chart",
        },
        "report": {"build_report", "build_insights", "make_insight", "related_metrics"},
    }

    def group_of(name: str) -> str:
        return next((g for g, names in groups.items() if name in names), "other")

    out: list[dict[str, Any]] = []
    for name in skill_lib.__all__:
        if name in mechanics:
            continue
        fn = getattr(skill_lib, name, None)
        if not callable(fn):
            continue
        doc = (inspect.getdoc(fn) or "").split("\n")[0]
        try:
            sig = str(inspect.signature(fn))
        except (TypeError, ValueError):
            sig = "()"
        out.append({"name": name, "group": group_of(name), "doc": doc, "signature": sig})
    return {"skills": out}


def _enrich_catalog_with_known_docs(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill descriptions for dbt-known tables after pg_catalog introspection."""
    known = {(t["schema"], t["table"]): t for t in get_catalog(role="admin")}
    for table in tables:
        doc = known.get((table["schema"], table["table"]))
        if doc is None:
            continue
        table["description"] = table.get("description") or doc.get("description")
        columns = {c["name"]: c for c in doc.get("columns", [])}
        for column in table.get("columns", []):
            col_doc = columns.get(column["name"])
            if col_doc is not None:
                column["description"] = column.get("description") or col_doc.get("description")
    return tables


@app.get("/agent/schema")
async def agent_schema(role: str = "user") -> dict[str, Any]:
    """Structured catalog for the SQL editor's schema browser + autocomplete."""
    if role == "admin":
        try:
            live_catalog = _enrich_catalog_with_known_docs(await load_database_catalog())
            return {"tables": merge_catalogs(live_catalog, get_catalog(role="admin"))}
        except Exception:  # noqa: BLE001 — keep the editor usable if catalog introspection fails
            return {"tables": get_catalog(role="admin")}
    return {"tables": get_catalog(role=role)}


# ---------------------------------------------------------------------------
# Architecture tab (M5, agent_sdk migration): a live snapshot of THIS system —
# runtime/model/quotas, the knowledge base index, and the tool registry — for
# the Data Pilot UI that visualises the GenAI system itself. Deliberately reads
# the same pure, dependency-light functions workspace.py calls (list_marts(),
# describe_table(), knowledge.load_pages()) directly, WITHOUT ever calling
# workspace.build_workspace()/workspace() — no per-run temp directory is
# created just to answer this endpoint.
# ---------------------------------------------------------------------------

_WORKSPACE_TEMPLATE_PATH = Path(__file__).resolve().parent / "prompts" / "workspace_claude.md"


def _template_bytes() -> bytes:
    return _WORKSPACE_TEMPLATE_PATH.read_bytes() if _WORKSPACE_TEMPLATE_PATH.exists() else b""


class ArchitectureRuntime(BaseModel):
    agent_runtime: str  # "pydantic_ai" (champion) | "agent_sdk" (challenger)
    model: str
    provider: str
    sandbox_runtime: str
    quotas: dict[str, int]
    fingerprint: dict[str, str]  # version.build_fingerprint() — av-* + component hashes


class KnowledgeFile(BaseModel):
    kind: str  # claude_md | marts | layouts | schema | knowledge
    id: str  # what to pass as `name` to /agent/architecture/content ("" if n/a)
    filename: str  # the name this shows up as inside a real run workspace
    label: str
    description: str = ""
    size: int
    sha256: str | None = None


class ArchitectureKnowledge(BaseModel):
    knowledge_version: str
    files: list[KnowledgeFile]


class ArchitectureTool(BaseModel):
    kind: str  # mcp | builtin
    name: str
    server: str | None = None
    description: str
    input_schema: dict[str, Any] | None = None
    quota: str
    guardrail: str


class ArchitectureResponse(BaseModel):
    runtime: ArchitectureRuntime
    knowledge: ArchitectureKnowledge
    tools: list[ArchitectureTool]


def _active_model_and_provider() -> tuple[str, str]:
    """The model + provider label actually answering questions right now.

    Mirrors ``sdk_agent.answer_with_sdk``'s model choice and ``_run_agent``'s
    runtime dispatch — the SDK runtime drives ``sdk_model`` through the Claude
    Code CLI (subscription/OAuth auth, not an API key); the champion uses
    whichever provider key ``LLM_PROVIDER`` selects.
    """
    s = settings
    if s.agent_runtime == "agent_sdk":
        return s.sdk_model, "anthropic (Claude Agent SDK / Claude Code CLI)"
    model = s.deepseek_model if s.llm_provider == "deepseek" else s.model
    return model, s.llm_provider


def _tool_registry() -> list[ArchitectureTool]:
    """The live MCP + built-in tool surface, generated from sdk_agent's own
    definitions — TOOL_DESCRIPTIONS/TOOL_SCHEMAS/GOVERNED_TOOLS/BUILTIN_TOOLS
    are the exact constants ``build_tool_server`` wires up for a real run, so
    this registry can't drift from what the agent_sdk runtime actually exposes.
    """
    guardrails = {
        "extract": "sql_guardrails + RLS — governed SELECT through the read-only agent_ro role",
        "run_analysis": (
            f"sandbox ({settings.sandbox_runtime}) — isolated pandas execution, skills.* preferred"
        ),
        "lookup_values": "sql_guardrails — read-only distinct-value lookup",
        "no_answer": "none — declarative refusal, no execution",
        "remember": "per-user memory store (pgvector embeddings), no SQL",
        "start_deck": (
            "Google Sheets/Slides via the service's own OAuth credential — the only "
            "third-party egress; never sandbox code; public sharing gated by DECK_PUBLIC"
        ),
        "add_slide": (
            "layout name must be in the curated catalogue (layouts.md); one atomic "
            "batchUpdate per slide"
        ),
    }
    quotas = {
        "extract": f"{settings.max_sql_attempts} attempts/run (MAX_SQL_ATTEMPTS)",
        "run_analysis": f"{settings.sandbox_run_attempts} attempts/run (SANDBOX_RUN_ATTEMPTS)",
        "lookup_values": "unmetered",
        "no_answer": "unmetered",
        "remember": "unmetered",
        "start_deck": "once per run",
        "add_slide": f"{settings.max_slides} slides/run (MAX_SLIDES)",
    }
    builtin_descriptions = {
        "Read": (
            "Read one file in the run workspace (CLAUDE.md, marts.md, layouts.md, "
            "schema/*.md, knowledge/*.md, frames/*.head.csv)."
        ),
        "Grep": "Search file contents across the run workspace.",
        "Glob": "List files in the run workspace by pattern.",
    }
    tools = [
        ArchitectureTool(
            kind="mcp",
            name=name,
            server=sdk_agent.MCP_SERVER,
            description=sdk_agent.TOOL_DESCRIPTIONS[name],
            input_schema=sdk_agent.TOOL_SCHEMAS[name],
            quota=quotas.get(name, "—"),
            guardrail=guardrails.get(name, "—"),
        )
        for name in sdk_agent.governed_tools()
    ]
    tools += [
        ArchitectureTool(
            kind="builtin",
            name=name,
            server=None,
            description=builtin_descriptions.get(name, ""),
            input_schema=None,
            quota=(
                f"{settings.max_knowledge_reads} knowledge/ page reads/run "
                "(MAX_KNOWLEDGE_READS); unmetered elsewhere in the workspace"
            ),
            guardrail="workspace-scoped filesystem — no Bash/Write/Edit, no host/network access",
        )
        for name in sdk_agent.BUILTIN_TOOLS
    ]
    return tools


def _layouts_md() -> str:
    """layouts.md exactly as a run would see it, or "" when deck export is off.

    Uses the process-wide catalogue cache sdk_agent keeps, so this never pays a
    Slides round trip of its own — if no run has loaded the pack yet it falls
    back to the built-in catalogue, which is what an unreadable pack yields too.
    """
    if not sdk_agent.deck_enabled():
        return ""
    catalogue = sdk_agent._catalogue_cache.get(settings.google_slides_template_id)
    return render_layouts_md(catalogue or DEFAULT_CATALOGUE)


def _knowledge_files() -> list[KnowledgeFile]:
    """Every file a real run workspace would contain, without building one.

    Mirrors ``workspace.build_workspace``'s layout 1:1: CLAUDE.md, marts.md,
    one schema/<schema>_<table>.md per user-visible table, and the knowledge
    tree — each computed through the same pure functions that module calls.
    """
    template_bytes = _template_bytes()
    marts_text = list_marts()
    files = [
        KnowledgeFile(
            kind="claude_md",
            id="",
            filename="CLAUDE.md",
            label="CLAUDE.md — workflow instructions template",
            description="Rendered per-run as the agent_sdk runtime's system prompt.",
            size=len(template_bytes),
            sha256=hashlib.sha256(template_bytes).hexdigest(),
        ),
        KnowledgeFile(
            kind="marts",
            id="",
            filename="marts.md",
            label="marts.md — mart index",
            description="Tier 0: table names + one-line purpose, always in context.",
            size=len(marts_text),
        ),
    ]
    for t in get_catalog(role="user"):
        if t["schema"] not in USER_VISIBLE_SCHEMAS:
            continue
        rel = f"{t['schema']}.{t['table']}"
        doc = describe_table(rel)
        files.append(
            KnowledgeFile(
                kind="schema",
                id=rel,
                filename=f"{t['schema']}_{t['table']}.md",
                label=rel,
                description=t.get("description") or "",
                size=len(doc),
            )
        )
    layouts_md = _layouts_md()
    if layouts_md:
        files.append(
            KnowledgeFile(
                kind="layouts",
                id="",
                filename="layouts.md",
                label="layouts.md — slide layout catalogue",
                description=(
                    "Enabled layouts from the slide pack; the agent Greps this before "
                    "add_slide. Curating it moves the av-* fingerprint."
                ),
                size=len(layouts_md),
                sha256=hashlib.sha256(layouts_md.encode()).hexdigest(),
            )
        )
    for p in load_pages():
        files.append(
            KnowledgeFile(
                kind="knowledge",
                id=p.rel_path,
                filename=p.rel_path,
                label=p.name,
                description=p.description,
                size=len(p.raw or p.body),
            )
        )
    return files


@app.get("/agent/architecture", response_model=ArchitectureResponse)
async def agent_architecture() -> ArchitectureResponse:
    """A live snapshot of the running system for the Architecture tab (M5).

    Runtime/model/quotas from settings, the knowledge base index (files a real
    run workspace would contain), and the tool registry generated from
    sdk_agent's own tool definitions — nothing here is hand-duplicated, and
    nothing builds a per-run workspace (see ``_knowledge_files``).
    """
    model, provider = _active_model_and_provider()
    return ArchitectureResponse(
        runtime=ArchitectureRuntime(
            agent_runtime=settings.agent_runtime,
            model=model,
            provider=provider,
            sandbox_runtime=settings.sandbox_runtime,
            quotas={
                "max_sql_attempts": settings.max_sql_attempts,
                "sandbox_run_attempts": settings.sandbox_run_attempts,
                "agent_request_limit": settings.agent_request_limit,
                "max_knowledge_reads": settings.max_knowledge_reads,
                "max_slides": settings.max_slides if sdk_agent.deck_enabled() else 0,
            },
            fingerprint=build_fingerprint(),
        ),
        knowledge=ArchitectureKnowledge(
            knowledge_version=knowledge_version(),
            files=_knowledge_files(),
        ),
        tools=_tool_registry(),
    )


@app.get("/agent/architecture/content")
async def agent_architecture_content(kind: str, name: str = "") -> dict[str, str]:
    """One knowledge-base file's body, for the Architecture tab's detail pane.

    ``kind``/``name`` are exactly the ``kind``/``id`` a ``KnowledgeFile`` from
    ``GET /agent/architecture`` carries, so the frontend never has to construct
    a path itself.
    """
    if kind == "claude_md":
        return {"content": _template_bytes().decode("utf-8", errors="replace")}
    if kind == "marts":
        return {"content": list_marts()}
    if kind == "layouts":
        return {"content": _layouts_md()}
    if kind == "schema":
        return {"content": describe_table(name)}
    if kind == "knowledge":
        return {"content": read_knowledge(name)}
    raise HTTPException(status_code=400, detail=f"unknown kind: {kind!r}")
