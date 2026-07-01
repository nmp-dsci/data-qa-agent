from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from .claude_agent import maybe_answer_with_claude
from .config import settings
from .db import engine, run_select
from .nl2sql import build_sql, phrase_answer
from .sql_guardrails import UnsafeSQLError


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()


app = FastAPI(title="data-qa-agent :: data-agent", version="0.1.0", lifespan=lifespan)


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
    engine: str = "stub"


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "provider": "claude" if settings.anthropic_api_key else "stub"}


@app.post("/agent/ask", response_model=AgentAnswer)
async def agent_ask(body: AskRequest) -> AgentAnswer:
    user_id = body.user.id

    # Preferred path: Claude (when configured). Falls back to the offline stub.
    claude = await maybe_answer_with_claude(body.question, user_id=user_id)
    if claude is not None:
        return AgentAnswer(**claude)

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
