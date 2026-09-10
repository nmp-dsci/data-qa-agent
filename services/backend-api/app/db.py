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

    Passing None sets an EMPTY context, which means ``app.current_user_id()``
    returns NULL and every RLS-protected table returns **zero rows**. That is the
    right default for a writer touching only un-RLS'd tables (the ops ingest
    endpoints), and completely wrong for anything that needs to read across
    users — see admin_ro_connection below.
    """
    async with engine.connect() as conn:
        async with conn.begin():
            await conn.execute(
                text("SELECT set_config('app.current_user_id', :uid, true)"),
                {"uid": user_id or ""},
            )
            yield conn


# The elevated read-only role from migration 0012: NOSUPERUSER, **BYPASSRLS**,
# SELECT on every schema. Created for the admin SQL editor; reused here by the ops
# rollup, which is an aggregate over ALL users by definition.
#
# This exists because the alternative is worse. The rollup used to run on
# rls_connection(None) — an empty RLS context — which silently returned zero rows
# for query_runs/messages/events/answer_feedback, so a background refresh would
# overwrite a good rollup with an all-zero one and blank the deck. Depending on
# "whichever admin happened to trigger it" would be just as fragile, and the
# machine-token refresh (a scheduler, the deploy workflow) has no user at all.
# A role whose whole purpose is "read everything, write nothing" is the honest
# tool for a cross-user aggregate.
admin_ro_engine = create_async_engine(
    settings.admin_ro_database_url,
    poolclass=NullPool,
    future=True,
    connect_args=_connect_args,
)


@asynccontextmanager
async def admin_ro_connection() -> AsyncIterator[AsyncConnection]:
    """Yield a read-only, RLS-bypassing connection for cross-user aggregates.

    SELECT-only by role grant for reads, so nothing reached through here can
    read across users by accident. Reserved for the small, deliberate set of
    surfaces that legitimately need to see across every user (the ops rollup
    refresh, the admin-only Analytics summary): every ordinary user-facing
    read stays on rls_connection so isolation is enforced by the database as
    usual. Any new caller that *reads* through this connection must sit behind
    require_admin (never a demo-mode bypass) — this is the one connection
    where RLS isn't the backstop.

    One narrow exception since migration 0038: this role also has INSERT on
    app.artifact_snapshots/app.artifact_edits and UPDATE on three
    app.query_runs columns — grants scoped to exactly those tables/columns for
    the handover poller and the /ask artifact-baseline write (routers/ask.py),
    neither of which needs RLS bypass to see other users' data, only to write
    a row the requesting user's own RLS policy doesn't grant INSERT on. That
    write path does not need require_admin: it writes only the current
    request's own run_id, nothing cross-user.
    """
    async with admin_ro_engine.connect() as conn:
        yield conn


def jsonable(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value
