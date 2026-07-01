from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from .config import settings
from .sql_guardrails import validate_select

_connect_args = {"ssl": True} if settings.db_ssl else {}
engine = create_async_engine(
    settings.agent_database_url, pool_pre_ping=True, future=True, connect_args=_connect_args
)


def _jsonable(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


async def run_select(sql: str, *, user_id: str) -> dict[str, Any]:
    """Execute a validated SELECT under the caller's RLS context (as agent_ro)."""
    safe = validate_select(sql)
    async with engine.connect() as conn:
        async with conn.begin():
            await conn.execute(
                text("SELECT set_config('app.current_user_id', :uid, true)"),
                {"uid": user_id or ""},
            )
            result = await conn.execute(text(safe))
            columns = list(result.keys())
            raw_rows = result.fetchmany(settings.max_rows)
    rows = [[_jsonable(v) for v in row] for row in raw_rows]
    return {"columns": columns, "rows": rows, "row_count": len(rows), "sql": safe}
