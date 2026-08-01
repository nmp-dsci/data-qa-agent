"""Data Pilot MCP server (s35 rung 3).

Exposes the governed property-data surface to any MCP client — Claude Code, a
desktop client, another agent — over streamable HTTP.

The deliberate architectural choice: this server holds **no database
credentials**. Every tool is an HTTP call to backend-api carrying this server's
own ``dpk_`` service key, so the SQL guardrails, RLS scoping, daily cap and audit
trail live in exactly one place. A second implementation of any of those is a
second thing to get wrong, and they would drift.

Access follows the key: whatever datasets the ``surface='mcp'`` service account
is granted is precisely what these tools can reach. There is no ambient
authority and no way for a tool argument to widen scope.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from .config import settings

log = logging.getLogger(__name__)

mcp = MCPServer(
    "data-pilot",
    instructions=(
        "Governed access to NSW property market data (sales, rentals, yields). "
        "Call list_datasets first to see what this connection can read, then "
        "ask_question for anything analytical — it writes the SQL for you and "
        "handles metric definitions and units. Drop to run_governed_query only "
        "when you need a specific SELECT of your own."
    ),
)

# Rows a tool will hand back to a model. The governed query path already caps
# server-side; this is a second bound so a wide result can't blow up a client's
# context window.
MAX_ROWS = 200


class BackendError(RuntimeError):
    """A tool-visible failure. The message goes to the model, so it must be plain."""


async def _call(method: str, path: str, **kwargs: Any) -> Any:
    if not settings.mcp_service_key:
        raise BackendError(
            "This MCP server has no service key configured, so it cannot reach Data Pilot. "
            "Set MCP_SERVICE_KEY to a key minted for the 'mcp' surface."
        )
    headers = {
        "Authorization": f"Bearer {settings.mcp_service_key}",
        "X-Client-Channel": "mcp",
    }
    async with httpx.AsyncClient(timeout=settings.request_timeout_s) as client:
        resp = await client.request(
            method, f"{settings.backend_url}{path}", headers=headers, **kwargs
        )
    if resp.status_code == 401:
        raise BackendError("Data Pilot rejected this server's service key (401).")
    if resp.status_code == 403:
        raise BackendError("This key is not permitted to use that endpoint (403).")
    if resp.status_code == 429:
        raise BackendError("Daily question limit reached for this service account.")
    if resp.status_code >= 400:
        raise BackendError(f"Data Pilot returned {resp.status_code}: {resp.text[:200]}")
    return resp.json()


@mcp.tool()
async def list_datasets() -> list[dict[str, Any]]:
    """List the property datasets this connection can read.

    Start here. The slugs returned are the only valid inputs to describe_schema,
    and they reflect this key's grants — not every dataset that exists.
    """
    data = await _call("GET", "/explore/datasets")
    return [
        {
            "slug": d.get("slug"),
            "name": d.get("name"),
            "time_dimension": d.get("time_dim"),
            "default_metric": d.get("default_metric"),
            "metrics": [m.get("name") for m in (d.get("metrics") or [])],
        }
        for d in (data.get("datasets") or [])
    ]


@mcp.tool()
async def describe_schema(dataset_slug: str) -> dict[str, Any]:
    """Describe the tables and columns available for a dataset.

    Call this before writing SQL with run_governed_query, so column names and
    types come from the live schema rather than a guess.
    """
    catalog = await _call("GET", "/schema/catalog")
    tables = [
        t
        for t in (catalog.get("tables") or [])
        if not dataset_slug or dataset_slug in str(t.get("dataset", dataset_slug))
    ]
    return {"dataset": dataset_slug, "tables": tables}


@mcp.tool()
async def ask_question(question: str) -> dict[str, Any]:
    """Ask a natural-language question about the property data.

    This runs the full Data Pilot agent: it writes the SQL, runs it under row
    level security, and returns a written answer plus the SQL it used. Prefer
    this over run_governed_query for anything analytical — it handles the joins,
    the metric definitions and the units. Can take up to a minute or two.
    """
    data = await _call("POST", "/ask", json={"question": question})
    rows = data.get("rows") or []
    return {
        "answer": data.get("answer"),
        "sql": data.get("sql"),
        "columns": data.get("columns"),
        "rows": rows[:MAX_ROWS],
        "row_count": data.get("row_count"),
        "truncated": len(rows) > MAX_ROWS,
        "run_id": data.get("run_id"),
        # Surfaced rather than hidden: a degraded answer is one the agent
        # couldn't fully produce, and a model should be able to say so.
        "degraded": data.get("degraded", False),
    }


@mcp.tool()
async def run_governed_query(sql: str) -> dict[str, Any]:
    """Run a read-only SQL SELECT against the property marts.

    Every statement passes the same guardrails the app uses: SELECT only, single
    statement, allowlisted tables, and row level security scoped to this
    connection's grants. Anything else is rejected rather than partially run.
    Use describe_schema first to get real column names.
    """
    data = await _call("POST", "/sql", json={"sql": sql})
    # The SQL editor's contract is 200-with-an-error-field, not a 4xx, so the UI
    # can render a rejection inline. Left unchecked that turns a blocked
    # "DELETE FROM ..." into an empty SUCCESS as far as a model is concerned —
    # it would conclude the table is empty rather than that it was refused.
    if data.get("error"):
        raise BackendError(str(data["error"]))
    rows = data.get("rows") or []
    return {
        "columns": data.get("columns"),
        "rows": rows[:MAX_ROWS],
        "row_count": data.get("row_count", len(rows)),
        "truncated": len(rows) > MAX_ROWS,
    }


@mcp.tool()
async def get_audit(limit: int = 10) -> list[dict[str, Any]]:
    """Recent questions and queries run through this connection.

    Every call made through this server is recorded against its service account;
    this reads that trail back. Useful for checking what a previous session did.
    """
    data = await _call("GET", "/sql/history", params={"limit": max(1, min(limit, 50))})
    return [
        {
            "sql": r.get("sql"),
            "row_count": r.get("row_count"),
            "created_at": r.get("created_at"),
            "status": r.get("status"),
        }
        for r in (data or [])
    ]


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    # Streamable HTTP is the remote transport; stdio would only serve a client on
    # this same machine, which defeats the point of deploying it. The SDK's
    # DNS-rebinding protection defaults to localhost-only Host headers, which
    # would reject every request through a container port map or App Runner —
    # so the allowed hosts/origins are configured explicitly rather than
    # disabled.
    mcp.run(
        transport="streamable-http",
        host=settings.host,
        port=settings.port,
        transport_security=TransportSecuritySettings(
            allowed_hosts=settings.allowed_host_list,
            allowed_origins=settings.allowed_origin_list,
        ),
    )


if __name__ == "__main__":
    main()
