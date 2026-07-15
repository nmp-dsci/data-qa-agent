from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import logfire
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

# Configured before importing sandbox_agent: agent_common (pulled in by that
# module) instruments pydantic-ai/httpx at import time, which needs
# logfire.configure() to have already run.
logfire.configure(service_name="data-agent", send_to_logfire="if-token-present")

from . import analytics  # noqa: E402
from .chart import trend_overlay_encoding, validate_chart_spec  # noqa: E402
from .config import settings  # noqa: E402
from .db import admin_engine, engine, load_database_catalog, run_select  # noqa: E402
from .knowledge import knowledge_version  # noqa: E402
from .nl2sql import build_sql, phrase_answer  # noqa: E402
from .pages import chart_object_from_spec, compose_pages, page_plan, planned_kinds  # noqa: E402
from .provider import choose_provider  # noqa: E402
from .sandbox import run_code  # noqa: E402
from .sandbox.extract import extract  # noqa: E402
from .sandbox_agent import answer_with_sandbox  # noqa: E402
from .schema import get_catalog, merge_catalogs  # noqa: E402
from .sql_assist import sql_assist  # noqa: E402
from .sql_guardrails import UnsafeSQLError  # noqa: E402
from .titles import summarize_title  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
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
    # Ordered step-by-step trace (each SQL attempt/chart/memory) for admin inspection.
    steps: list[dict[str, Any]] = []
    # Structured InsightReport (K2) — present on the LLM path; None for the stub.
    report: dict[str, Any] | None = None
    # Pages contract (s07): Summary → Insights pages of governed objects
    # (data + intent) the frontend's template registry renders with visx.
    pages: list[dict[str, Any]] | None = None


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
    llm = await answer_with_sandbox(
        body.question, user_id=user_id, plan=body.user.plan, progress=progress
    )
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
        return AgentAnswer(
            answer=f"I couldn't run that safely: {exc}",
            sql=sql,
            input_tokens=salvage.get("input_tokens") if salvage else None,
            output_tokens=salvage.get("output_tokens") if salvage else None,
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
        input_tokens=salvage.get("input_tokens") if salvage else None,
        output_tokens=salvage.get("output_tokens") if salvage else None,
        report=report,
        pages=pages or None,
        steps=steps,
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
        return SqlResult(sql=body.sql, error=str(exc))
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
    report: dict[str, Any] | None, *, element_id: str, object_type: str = "compare"
) -> dict[str, Any] | None:
    """Lift a built object's report into ONE page object with a stable element_id.

    Charts lift their ``main_chart`` (combo-aware, via chart_object_from_spec);
    kpi/headline objects carry no chart, so the first headline tile is lifted."""
    if not isinstance(report, dict):
        return None
    spec = report.get("main_chart")
    if spec:
        lifted = chart_object_from_spec(spec, element_id=element_id, role="chart", height="md")
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


def _run_named_objects(objects: list[dict[str, Any]], frame: Any) -> list[dict[str, Any]]:
    """Run each named object's run_analysis snippet against the extract + lift it."""
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
            obj = _lift_object(outcome.report, element_id=eid, object_type=otype)
            out.append(
                {"element_id": eid, "object": obj, "error": None if obj else outcome.error}
            )
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
    objects_out = _run_named_objects(body.objects, frame)
    if not body.code.strip():
        return AnalysisResponse(
            columns=columns, rows=rows, row_count=row_count, objects_out=objects_out
        )

    outcome = run_code(body.code, df=frame, frames={"extract": frame})
    # Compose renderable pages from the produced report so the Builder can add
    # this sandbox run's output as a report page (the same PageLayout as chat).
    pages: list[dict[str, Any]] = []
    if outcome.report:
        try:
            pages, _ = compose_pages(outcome.report)
        except Exception:  # noqa: BLE001 — page composition is best-effort here
            pages = []
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
        error=outcome.error,
    )


class AnalysisObjectRequest(BaseModel):
    sql: str
    code: str = ""
    object_type: str = "compare"
    instruction: str
    # s16 full cascade: the golden's current presentation objects (digest) + which
    # one is being edited, so the agent rebuilds the WHOLE report (not one object)
    # and we lift the right object back into the presentation.
    objects: list[dict[str, Any]] = []
    target_element_id: str | None = None
    user: UserCtx


class AnalysisObjectResponse(BaseModel):
    code: str = ""
    # The extract that produced this result — the revised SQL when the agent had
    # to add columns for the requested data, else the caller's SQL unchanged.
    sql: str = ""
    object: dict[str, Any] | None = None
    report: dict[str, Any] | None = None
    # The FULL recomposed report as pages (every object with real data), so the
    # builder can refresh the whole presentation in sync — not just one object.
    pages: list[dict[str, Any]] | None = None
    columns: list[str] = []
    rows: list[list[Any]] = []
    reasoning: list[dict[str, Any]] = []
    engine: str = "stub"
    skills_used: list[str] = []
    skill_gaps: list[dict[str, Any]] = []
    error: str | None = None


_CHART_TYPES = {"trend", "breakdown", "compare"}


def _chart_sig(data: dict[str, Any]) -> tuple[str, str, str, str]:
    """A chart's field signature — dimension/measure/line/group, from bar OR trend
    shape — so we can tell which recomposed chart is the one the curator edited."""
    dim = str(data.get("dimension") or data.get("x") or "")
    meas = str(data.get("measure") or data.get("y") or "")
    line = str(data.get("line_measure") or "")
    grp = str(data.get("group") or data.get("series") or "")
    return (dim, meas, line, grp)


def _lift_target(
    pages: list[dict[str, Any]],
    report: dict[str, Any] | None,
    target_element_id: str | None,
    existing: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Pick the object the curator was editing out of the recomposed pages.

    An explicit ``element_id`` match wins (editing an existing, stably-id'd
    object). Otherwise the target is the chart whose field signature isn't among
    the existing presentation charts — i.e. the one this instruction newly built
    (robust to which chart the model made ``main_chart``). Returns ``None`` when no
    new-or-changed chart was produced, so a run that failed to honour the edit
    surfaces as an error rather than silently applying a stale duplicate.
    """
    flat = [o for p in pages for col in p.get("columns", []) for o in col if isinstance(o, dict)]
    if target_element_id:
        for obj in flat:
            if obj.get("element_id") == target_element_id:
                return obj
    charts = [o for o in flat if o.get("type") in _CHART_TYPES]
    existing_sigs: set[tuple[str, str, str, str]] = set()
    for obj in existing or []:
        data = obj.get("data") if isinstance(obj, dict) else None
        if obj.get("type") in _CHART_TYPES and isinstance(data, dict):
            if data.get("dimension") or data.get("x"):
                existing_sigs.add(_chart_sig(data))
    for obj in charts:
        if _chart_sig(obj.get("data") or {}) not in existing_sigs:
            return obj
    # No id match and no new/changed chart. If the golden had NO charts before,
    # this is the first-object case → lift the report's main_chart. Otherwise the
    # edit didn't take (every chart matches a pre-existing one) → None (error).
    if not existing_sigs:
        spec = report.get("main_chart") if isinstance(report, dict) else None
        lifted = chart_object_from_spec(
            spec, element_id="authored:chart", role="chart", height="md"
        )
        return lifted.model_dump(exclude_none=True) if lifted is not None else None
    return None


@app.post("/agent/analysis/object", response_model=AnalysisObjectResponse)
async def agent_analysis_object(body: AnalysisObjectRequest) -> AnalysisObjectResponse:
    """Author ONE report object from a plain-English instruction (Golden Examples).

    Codegen (``scaffold_object``) rewrites run_analysis to build exactly the chart
    the curator described, then it runs in the SAME governed extract + sandbox path
    as ``/agent/analysis``; the produced ``main_chart`` is lifted back into a page
    object (combo-aware) so the builder can drop real, sandbox-computed data into
    the presentation. Never raises — errors travel on ``error`` for the builder.
    """
    from .object_codegen import scaffold_object

    try:
        frame, meta = await extract(body.sql, user_id=body.user.id)
    except UnsafeSQLError as exc:
        return AnalysisObjectResponse(sql=body.sql, error=f"extract rejected: {exc}")
    except Exception as exc:  # noqa: BLE001 — surface DB/extract errors to the builder
        return AnalysisObjectResponse(sql=body.sql, error=f"extract failed: {exc}")

    columns = meta.get("columns", [])
    rows = meta.get("rows", [])
    # The agent rewrites the WHOLE report (every object + the change) and may
    # return a revised SQL when the requested data isn't in the extract. It runs
    # the extract/sandbox tools to verify before finalizing (s16).
    gen = await scaffold_object(
        instruction=body.instruction,
        object_type=body.object_type,
        columns=columns,
        code=body.code,
        sql=body.sql,
        objects=body.objects,
        user_id=body.user.id,
        frame=frame,
    )
    code = str(gen.get("code") or "")
    reasoning = gen.get("reasoning", [])
    engine = str(gen.get("engine") or "stub")
    if not code.strip():
        return AnalysisObjectResponse(
            sql=body.sql,
            columns=columns,
            rows=rows,
            reasoning=reasoning,
            engine=engine,
            error=gen.get("error") or "no code generated",
        )

    # Apply a revised extract, if the agent produced one — all-or-nothing: a bad
    # SQL leaves every stage on the caller's original (no partial write).
    effective_sql = body.sql
    new_sql = str(gen.get("sql") or "").strip()
    if new_sql and new_sql != body.sql.strip():
        try:
            frame, meta = await extract(new_sql, user_id=body.user.id)
            columns = meta.get("columns", [])
            rows = meta.get("rows", [])
            effective_sql = new_sql
        except UnsafeSQLError as exc:
            return AnalysisObjectResponse(
                sql=body.sql,
                columns=columns,
                rows=rows,
                reasoning=reasoning,
                engine=engine,
                error=f"revised extract rejected: {exc}",
            )
        except Exception as exc:  # noqa: BLE001 — surface to the builder, keep old SQL
            return AnalysisObjectResponse(
                sql=body.sql,
                columns=columns,
                rows=rows,
                reasoning=reasoning,
                engine=engine,
                error=f"revised extract failed: {exc}",
            )

    outcome = run_code(code, df=frame, frames={"extract": frame})
    pages: list[dict[str, Any]] = []
    if outcome.report and isinstance(outcome.report, dict):
        try:
            pages, _ = compose_pages(outcome.report)
        except Exception:  # noqa: BLE001 — page composition is best-effort here
            pages = []
    obj = _lift_target(pages, outcome.report, body.target_element_id, body.objects)
    return AnalysisObjectResponse(
        code=code,
        sql=effective_sql,
        object=obj,
        report=outcome.report,
        pages=pages or None,
        columns=columns,
        rows=rows,
        reasoning=reasoning,
        engine=engine,
        skills_used=outcome.skills_used,
        skill_gaps=[g.model_dump() for g in outcome.skill_gaps],
        error=outcome.error or gen.get("error"),
    )


class AnalysisBuildObjectRequest(BaseModel):
    sql: str
    name: str
    object_type: str = "compare"
    # Structured form state (grain, dimension, group, bar/line measures + windows)
    # the deterministic builder emits code from — see agent.object_builder.
    spec: dict[str, Any] = {}
    # Optional NL instruction — when set, the DeepSeek scaffold_object path authors
    # the code instead of the deterministic builder (relabelled with this name).
    instruction: str = ""
    user: UserCtx


class AnalysisBuildObjectResponse(BaseModel):
    name: str = ""
    element_id: str = ""
    object_type: str = "compare"
    # The extract that produced this — extended to add the object's columns when
    # they weren't already SELECTed, else the caller's SQL unchanged.
    sql: str = ""
    code: str = ""
    object: dict[str, Any] | None = None
    columns: list[str] = []
    rows: list[list[Any]] = []
    skills_used: list[str] = []
    skill_gaps: list[dict[str, Any]] = []
    error: str | None = None


@app.post("/agent/analysis/build-object", response_model=AnalysisBuildObjectResponse)
async def agent_analysis_build_object(
    body: AnalysisBuildObjectRequest,
) -> AnalysisBuildObjectResponse:
    """Deterministically build a NAMED presentation object (s18 Golden Sandbox).

    The builder emits run_analysis from the ``spec`` (or delegates to the NL
    scaffold path), *extends the shared extract* when the object needs columns it
    lacks (carrying the golden's suburb/property filters), runs it in the governed
    sandbox, and lifts the ``main_chart`` back into a page object with the object's
    stable ``element_id`` so the report can link to it by name.
    """
    from .object_builder import (
        build_object_code,
        canonical_extract_sql,
        element_id_for,
        needed_columns,
    )

    eid = element_id_for(body.name)

    def _err(msg: str, *, sql: str, code: str = "") -> AnalysisBuildObjectResponse:
        return AnalysisBuildObjectResponse(
            name=body.name,
            element_id=eid,
            object_type=body.object_type,
            sql=sql,
            code=code,
            error=msg,
        )

    # 1. Run the current extract; extend it if the object needs columns it lacks.
    # A failing base SQL (or a placeholder) doesn't block a deterministic build —
    # when a spec is given we fall through to the canonical grain-level extract.
    frame: Any = None
    meta: dict[str, Any] = {}
    columns: list[str] = []
    base_error: str | None = None
    try:
        frame, meta = await extract(body.sql, user_id=body.user.id)
        columns = meta.get("columns", [])
    except UnsafeSQLError as exc:
        base_error = f"extract rejected: {exc}"
    except Exception as exc:  # noqa: BLE001 — surface DB/extract errors to the builder
        base_error = f"extract failed: {exc}"
    effective_sql = body.sql
    need = needed_columns(body.spec)
    must_rewrite = bool(body.spec) and (base_error is not None or not need.issubset(set(columns)))
    if must_rewrite:
        grain = body.spec.get("grain") or ["suburb", "area_band", "month"]
        new_sql = canonical_extract_sql(
            body.sql,
            grain=grain,
            measure_source_cols=need,
            where_override=str(body.spec.get("filter") or ""),
        )
        try:
            frame, meta = await extract(new_sql, user_id=body.user.id)
            columns = meta.get("columns", [])
            effective_sql = new_sql
        except UnsafeSQLError as exc:
            return _err(f"revised extract rejected: {exc}", sql=body.sql)
        except Exception as exc:  # noqa: BLE001 — keep the caller's SQL on failure
            return _err(f"revised extract failed: {exc}", sql=body.sql)
    elif base_error is not None:
        # No spec to build a canonical extract from — surface the base failure.
        return _err(base_error, sql=body.sql)

    # 2. Code: the deterministic builder, or the NL scaffold path when instructed.
    if body.instruction.strip():
        from .object_codegen import scaffold_object

        gen = await scaffold_object(
            instruction=body.instruction,
            object_type=body.object_type,
            columns=columns,
            code="",
            sql=effective_sql,
            objects=None,
            user_id=body.user.id,
            frame=frame,
        )
        code = str(gen.get("code") or "")
        new_sql = str(gen.get("sql") or "").strip()
        if new_sql and new_sql != effective_sql.strip():
            try:
                frame, meta = await extract(new_sql, user_id=body.user.id)
                columns = meta.get("columns", [])
                effective_sql = new_sql
            except Exception as exc:  # noqa: BLE001
                return _err(f"revised extract failed: {exc}", sql=effective_sql, code=code)
        if not code.strip():
            return _err(gen.get("error") or "no code generated", sql=effective_sql)
    else:
        code = build_object_code(object_type=body.object_type, spec=body.spec)

    # 3. Run + lift the object.
    outcome = run_code(code, df=frame, frames={"extract": frame})
    obj = _lift_object(outcome.report, element_id=eid, object_type=body.object_type)
    return AnalysisBuildObjectResponse(
        name=body.name,
        element_id=eid,
        object_type=body.object_type,
        sql=effective_sql,
        code=code,
        object=obj,
        columns=columns,
        rows=meta.get("rows", []),
        skills_used=outcome.skills_used,
        skill_gaps=[g.model_dump() for g in outcome.skill_gaps],
        error=outcome.error if obj is not None else (outcome.error or "object produced no chart"),
    )


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


class ScaffoldRequest(BaseModel):
    question: str = ""
    columns: list[str] = []
    skills: list[str] = []


class ScaffoldResponse(BaseModel):
    code: str = ""
    reasoning: list[dict[str, Any]] = []
    engine: str = "stub"
    error: str | None = None


@app.post("/agent/skills/scaffold", response_model=ScaffoldResponse)
async def agent_skills_scaffold(body: ScaffoldRequest) -> ScaffoldResponse:
    """Regenerate run_analysis code from a chosen set of skills, with a reason per
    skill (s14 Golden Examples). The model writes the code using exactly those skills."""
    from .skill_codegen import scaffold_from_skills

    out = await scaffold_from_skills(
        question=body.question, columns=body.columns, skills=body.skills
    )
    return ScaffoldResponse(**out)


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
