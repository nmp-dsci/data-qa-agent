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
