"""Local governed SQL executor (s38 P4) — the demo-mode port of the agent's.

The SQL editor has always executed through the data-agent's ``/agent/sql``,
which is a *non-LLM* endpoint: validate, run read-only, cap. Demo deployments
delete the data-agent service entirely, so this module carries the same
executor inside backend-api — same guardrails (``sql_guardrails`` is a verbatim
copy of the agent's), same two roles (``agent_ro`` RLS-scoped / ``admin_ro``
BYPASSRLS), tighter demo limits (5s / 500 rows vs the agent's 6s / 5000).

Only the demo path uses this; a full deployment keeps delegating to the agent
so live behaviour is unchanged.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from .config import settings
from .sql_guardrails import validate_select

_connect_args = {"ssl": settings.db_ssl} if settings.db_ssl else {}
# NullPool for the same reason as everywhere else in this stack: an idle demo
# holds zero connections so Aurora can auto-pause.
_ro_engine = create_async_engine(
    settings.agent_ro_database_url, poolclass=NullPool, future=True, connect_args=_connect_args
)
_admin_ro_engine = create_async_engine(
    settings.admin_ro_database_url, poolclass=NullPool, future=True, connect_args=_connect_args
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
    """Execute a validated SELECT read-only with the demo timeout + row cap.

    Mirrors ``agent/db.py::run_select`` exactly: agent_ro under the caller's
    RLS context by default; admin_ro (BYPASSRLS) for admins only.
    """
    safe = validate_select(sql)
    engine = _admin_ro_engine if as_admin else _ro_engine
    async with engine.connect() as conn:
        async with conn.begin():
            if not as_admin:
                await conn.execute(
                    text("SELECT set_config('app.current_user_id', :uid, true)"),
                    {"uid": user_id or ""},
                )
            await conn.execute(
                text(f"SET LOCAL statement_timeout = {settings.demo_sql_statement_timeout_ms}")
            )
            result = await conn.execute(text(safe))
            columns = list(result.keys())
            raw_rows = result.fetchmany(settings.demo_sql_max_rows)
    rows = [[_jsonable(v) for v in row] for row in raw_rows]
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "sql": safe,
        "truncated": len(rows) >= settings.demo_sql_max_rows,
    }


# Non-admin visitors browse the governed analytical schemas only; admins see
# everything (matching the agent's role split, approximated by live
# introspection instead of its curated static catalog).
_VISITOR_SCHEMAS = ("marts", "staging")

_CATALOG_SQL = """
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
        a.attname AS column_name,
        format_type(a.atttypid, a.atttypmod) AS data_type,
        col_description(c.oid, a.attnum) AS column_description
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    LEFT JOIN pg_catalog.pg_attribute a
        ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
    WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
      AND n.nspname NOT IN ('information_schema', 'pg_catalog')
      AND n.nspname NOT LIKE 'pg_toast%'
      AND n.nspname NOT LIKE 'pg_temp_%'
    ORDER BY n.nspname, c.relname, a.attnum
"""


async def load_catalog(*, role: str) -> dict[str, Any]:
    """Table/column metadata for the SQL editor's schema browser."""
    async with _ro_engine.connect() as conn:
        result = await conn.execute(text(_CATALOG_SQL))
        rows = result.mappings().all()
    tables: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if role != "admin" and row["schema_name"] not in _VISITOR_SCHEMAS:
            continue
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
    return {"tables": list(tables.values())}
