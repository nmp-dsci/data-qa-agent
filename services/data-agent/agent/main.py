from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import logfire
from fastapi import FastAPI
from pydantic import BaseModel

# Configured before importing llm_agent: that module instruments pydantic-ai/
# httpx at import time, which needs logfire.configure() to have already run.
logfire.configure(service_name="data-agent", send_to_logfire="if-token-present")

from .config import settings  # noqa: E402
from .db import engine, run_select  # noqa: E402
from .llm_agent import maybe_answer_with_llm  # noqa: E402
from .nl2sql import build_sql, phrase_answer  # noqa: E402
from .provider import choose_provider  # noqa: E402
from .sql_guardrails import UnsafeSQLError  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
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
        return AgentAnswer(answer=f"I couldn't run that safely: {exc}", sql=sql)

    answer = phrase_answer(body.question, intent, result)
    return AgentAnswer(
        answer=answer,
        sql=result["sql"],
        columns=result["columns"],
        rows=result["rows"],
        row_count=result["row_count"],
        engine="stub",
    )
