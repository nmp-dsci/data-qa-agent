from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from .config import settings
from .sql_guardrails import validate_select

# Pass the sslmode string through (e.g. "require"): asyncpg treats ssl=True as
# verify-full, which fails against Aurora's RDS-CA-signed cert (not in the
# container trust store). "require" = encrypt without verification — libpq
# semantics, and what every other client in this stack does. Upgrade to
# verify-full + the RDS CA bundle in the harden phase.
_connect_args = {"ssl": settings.db_ssl} if settings.db_ssl else {}
engine = create_async_engine(
    settings.agent_database_url, pool_pre_ping=True, future=True, connect_args=_connect_args
)
# Elevated read-only engine for admin SQL-editor queries (admin_ro: BYPASSRLS,
# SELECT on every schema). Only role == "admin" run_select(..., as_admin=True)
# calls use it; the agent + regular users stay on `engine` (agent_ro, RLS-scoped).
admin_engine = create_async_engine(
    settings.admin_ro_database_url, pool_pre_ping=True, future=True, connect_args=_connect_args
)


def _jsonable(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


async def run_select(sql: str, *, user_id: str, as_admin: bool = False) -> dict[str, Any]:
    """Execute a validated SELECT, read-only, with a statement timeout + row cap.

    Default (``as_admin=False``): as ``agent_ro`` under the caller's RLS context —
    rows scoped to their datasets / own operational rows. ``as_admin=True`` (the
    admin SQL editor only): as ``admin_ro`` (BYPASSRLS, SELECT on every schema) so
    an admin can read any table and all rows. Still SELECT-only either way.
    """
    safe = validate_select(sql)
    active_engine = admin_engine if as_admin else engine
    async with active_engine.connect() as conn:
        async with conn.begin():
            # RLS context only matters for the scoped role; admin_ro bypasses RLS.
            if not as_admin:
                await conn.execute(
                    text("SELECT set_config('app.current_user_id', :uid, true)"),
                    {"uid": user_id or ""},
                )
            # SET LOCAL (not a bind param — Postgres doesn't allow parameterizing
            # this GUC) so a runaway or accidentally-unfiltered query against the
            # ~3M-row staging tables can't hang the connection; scoped to this
            # transaction only, same lifetime as the RLS session var above.
            await conn.execute(
                text(f"SET LOCAL statement_timeout = {settings.sql_statement_timeout_ms}")
            )
            result = await conn.execute(text(safe))
            columns = list(result.keys())
            raw_rows = result.fetchmany(settings.max_rows)
    rows = [[_jsonable(v) for v in row] for row in raw_rows]
    return {"columns": columns, "rows": rows, "row_count": len(rows), "sql": safe}


async def load_database_catalog() -> list[dict[str, Any]]:
    """Return table/column metadata for every non-system relation in the database.

    This is admin-facing discovery metadata only. Query execution still goes
    through run_select(), the read-only role, and table-level RLS/privileges.
    """
    sql = """
        SELECT
            n.nspname AS schema_name,
            c.relname AS table_name,
            CASE c.relkind
                WHEN 'r' THEN 'table'
                WHEN 'p' THEN 'partitioned table'
                WHEN 'v' THEN 'view'
                WHEN 'm' THEN 'materialized view'
                WHEN 'f' THEN 'foreign table'
                ELSE c.relkind::text
            END AS relation_type,
            obj_description(c.oid, 'pg_class') AS table_description,
            a.attnum AS ordinal_position,
            a.attname AS column_name,
            format_type(a.atttypid, a.atttypmod) AS data_type,
            col_description(c.oid, a.attnum) AS column_description
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_catalog.pg_attribute a
            ON a.attrelid = c.oid
           AND a.attnum > 0
           AND NOT a.attisdropped
        WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND n.nspname NOT IN ('information_schema', 'pg_catalog')
          AND n.nspname NOT LIKE 'pg_toast%'
          AND n.nspname NOT LIKE 'pg_temp_%'
        ORDER BY n.nspname, c.relname, a.attnum
    """
    async with engine.connect() as conn:
        result = await conn.execute(text(sql))
        rows = result.mappings().all()

    tables: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["schema_name"], row["table_name"])
        table = tables.setdefault(
            key,
            {
                "schema": row["schema_name"],
                "table": row["table_name"],
                "type": row["relation_type"],
                "description": row["table_description"],
                "columns": [],
            },
        )
        if row["column_name"]:
            table["columns"].append(
                {
                    "name": row["column_name"],
                    "type": row["data_type"],
                    "description": row["column_description"],
                }
            )
    return list(tables.values())
