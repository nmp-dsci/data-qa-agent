from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from .config import settings

# Pass the sslmode string through (e.g. "require"): asyncpg treats ssl=True as
# verify-full, which fails against Aurora's RDS-CA-signed cert (not in the
# container trust store). "require" = encrypt without verification — libpq
# semantics, and what every other client in this stack does. Upgrade to
# verify-full + the RDS CA bundle in the harden phase.
#
# timeout bounds the CONNECT phase only (asyncpg default is 60s). While Aurora
# Serverless resumes from auto-pause, connects hang silently for the whole
# resume (~20-30s observed in prod, s29) — every request in flight just sat
# there, indistinguishable from a dead app. A short connect timeout turns the
# hang into a classifiable failure (see is_db_waking) that the API maps to a
# retryable 503 instead. Queries on an established connection are unaffected.
_connect_args: dict[str, Any] = {"timeout": 5}
if settings.db_ssl:
    _connect_args["ssl"] = settings.db_ssl
# NullPool: close the connection when the request finishes instead of keeping a
# warm pool. An always-on App Runner instance would otherwise hold ~5 idle
# connections forever, so Aurora Serverless v2 never sees the zero-connection
# window it needs to auto-pause (scale to zero) — the dominant idle cost. A
# fresh connect per request costs a few ms, negligible at this app's traffic;
# pre-ping is unnecessary since every checkout is already a new connection.
engine = create_async_engine(
    settings.database_url, poolclass=NullPool, future=True, connect_args=_connect_args
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
