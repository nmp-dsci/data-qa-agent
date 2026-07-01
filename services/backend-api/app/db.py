from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from .config import settings

_connect_args = {"ssl": True} if settings.db_ssl else {}
engine = create_async_engine(
    settings.database_url, pool_pre_ping=True, future=True, connect_args=_connect_args
)


@asynccontextmanager
async def rls_connection(user_id: str | None) -> AsyncIterator[AsyncConnection]:
    """Yield a connection inside a transaction with the RLS context set.

    Every query on this connection is scoped to `user_id` by Postgres RLS.
    Using SET LOCAL (via set_config(..., true)) keeps pooled connections clean.
    """
    async with engine.connect() as conn:
        async with conn.begin():
            await conn.execute(
                text("SELECT set_config('app.current_user_id', :uid, true)"),
                {"uid": user_id or ""},
            )
            yield conn


def jsonable(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value
