from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import logfire
from fastapi import FastAPI
from pydantic import BaseModel

# Configured before importing llm_agent: that module instruments pydantic-ai/
# httpx at import time, which needs logfire.configure() to have already run.
logfire.configure(service_name="data-agent", send_to_logfire="if-token-present")

from .config import settings  # noqa: E402
from .db import engine, load_database_catalog, run_select  # noqa: E402
from .llm_agent import maybe_answer_with_llm  # noqa: E402
from .nl2sql import build_sql, phrase_answer  # noqa: E402
from .provider import choose_provider  # noqa: E402
from .schema import get_catalog, merge_catalogs  # noqa: E402
from .sql_assist import sql_assist  # noqa: E402
from .sql_guardrails import UnsafeSQLError  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await engine.dispose()


app = FastAPI(title="data-qa-agent :: data-agent", version="0.1.0", lifespan=lifespan)
logfire.instrument_fastapi(app)


class UserCtx(BaseModel):
    id: str
    role: str = "user"


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


@app.get("/health")
async def health() -> dict[str, str]:
    selected = choose_provider(
        settings.llm_provider, settings.deepseek_api_key, settings.anthropic_api_key
    )
    return {"status": "ok", "provider": selected[0] if selected else "stub"}


@app.post("/agent/ask", response_model=AgentAnswer)
async def agent_ask(body: AskRequest) -> AgentAnswer:
    user_id = body.user.id

    # Preferred path: the configured LLM provider. Falls back to the offline stub.
    llm = await maybe_answer_with_llm(body.question, user_id=user_id)
    if llm is not None:
        return AgentAnswer(**llm)

    sql, intent = build_sql(body.question)
    try:
        result = await run_select(sql, user_id=user_id)
    except UnsafeSQLError as exc:
        return AgentAnswer(
            answer=f"I couldn't run that safely: {exc}",
            sql=sql,
            steps=[{"kind": "sql", "attempt": 1, "sql": sql, "status": "error", "error": str(exc)}],
        )

    answer = phrase_answer(body.question, intent, result)
    return AgentAnswer(
        answer=answer,
        sql=result["sql"],
        columns=result["columns"],
        rows=result["rows"],
        row_count=result["row_count"],
        engine="stub",
        steps=[
            {
                "kind": "sql",
                "attempt": 1,
                "sql": result["sql"],
                "status": "success",
                "row_count": result["row_count"],
                "intent": intent,
            }
        ],
    )


@app.post("/agent/sql", response_model=SqlResult)
async def agent_sql(body: SqlRequest) -> SqlResult:
    """Run raw user SQL through the SAME governed executor the agent uses.

    run_select already enforces every guardrail: validate_select (SELECT-only,
    single statement), the read-only agent_ro role, SET LOCAL RLS context, a
    statement timeout, and the row cap. No new DB path is introduced — the SQL
    editor is the agent's executor with a keyboard instead of an LLM.
    """
    try:
        result = await run_select(body.sql, user_id=body.user.id)
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
