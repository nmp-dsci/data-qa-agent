"""The MCP front door, mounted inside backend-api (s36).

s35 shipped this as a standalone container that called backend-api over HTTP
with its own service key. That bought process isolation at the price of an extra
image, an extra App Runner service, a second key to rotate and an internal
network hop. This is the same surface, mounted at ``/mcp`` on the API that
already exists.

What did **not** change is the part that matters: the tools are adapters, not a
second implementation. Every one of them calls the very same handler the web UI
calls, so the SQL guardrails, RLS scoping, daily caps and audit writes stay in
exactly one place and cannot drift.

What **did** change, stated plainly rather than buried:

* The MCP surface now runs in the process that holds the database credentials.
  The standalone container's "holds no DB credentials" property is genuinely
  gone. The mitigation is that nothing in this module touches the database
  directly — it goes through the same handlers, under the same RLS context, as
  every other caller.
* Authentication moved *forward*. The standalone server accepted any MCP client
  that could reach its port and used its key only for the hop to backend-api;
  here the **client** must present a ``dpk_`` key minted for ``surface='mcp'``.
  An unauthenticated caller never reaches the transport at all.

Because the key gate now sits in front of the transport, the SDK's DNS-rebinding
host allowlist is defence-in-depth rather than the primary control — rebinding
exists to attack unauthenticated localhost servers, and this one has no
anonymous path. That is why it is off unless ``MCP_ALLOWED_HOSTS`` is set, which
also removes the two-pass deploy the standalone service needed (an App Runner
service cannot reference its own hostname, so the allowlist could not be derived
in Terraform).
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from typing import Any

from fastapi import HTTPException
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.types import Receive, Scope, Send

from .auth import CurrentUser, service_account_from_header
from .config import settings
from .routers.ask import run_question
from .routers.explore import list_datasets as explore_list_datasets
from .routers.sql import SqlRequest, run_sql, schema_catalog, sql_history

log = logging.getLogger(__name__)

CHANNEL = "mcp"

# Rows a tool hands back to a model. The governed path already caps server-side;
# this is a second bound so a wide result cannot blow up a client's context.
MAX_ROWS = 200

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

# Set by ServiceKeyGate on the way in, read by the tools. This works because the
# transport is mounted stateless (see build_mcp_app): every JSON-RPC call is
# handled inside its own request task, so the tool runs in the context the gate
# established. A session-mode transport would run tool calls in a long-lived
# task belonging to whichever request opened the session, and this would be the
# wrong identity — which is why the tools below fail closed rather than guess.
_current_user: ContextVar[CurrentUser | None] = ContextVar("mcp_current_user", default=None)


class BackendError(RuntimeError):
    """A tool-visible failure. The message goes to the model, so it must be plain."""


def _user() -> CurrentUser:
    user = _current_user.get()
    if user is None:
        # Never fall back to "some service account": an identity mix-up here is
        # a data-access bug, and a loud failure is the only safe default.
        raise BackendError("This MCP request has no authenticated identity.")
    return user


def _translate(exc: HTTPException) -> BackendError:
    """Turn a handler's HTTPException into something a model can act on."""
    if exc.status_code == 429:
        return BackendError(f"Rate limit reached: {exc.detail}")
    if exc.status_code in (401, 403):
        return BackendError(f"Not permitted: {exc.detail}")
    return BackendError(f"Data Pilot returned {exc.status_code}: {exc.detail}")


# ---------------------------------------------------------------------------
# Tools — each one delegates to the handler the web UI uses. No new SQL, no new
# guard, no new audit write.
# ---------------------------------------------------------------------------
@mcp.tool()
async def list_datasets() -> list[dict[str, Any]]:
    """List the property datasets this connection can read.

    Start here. The slugs returned are the only valid inputs to describe_schema,
    and they reflect this key's grants — not every dataset that exists.
    """
    try:
        data = await explore_list_datasets(user=_user())
    except HTTPException as exc:
        raise _translate(exc) from exc
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
    try:
        catalog = await schema_catalog(user=_user())
    except HTTPException as exc:
        raise _translate(exc) from exc
    tables = [
        t
        for t in (catalog.get("tables") or [])
        # Exact match on the dataset the table belongs to. A substring test here
        # over-returns whenever one slug is a prefix of another.
        if not dataset_slug or str(t.get("dataset", "")) in ("", dataset_slug)
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
    try:
        answer = await run_question(_user(), question, channel=CHANNEL)
    except HTTPException as exc:
        raise _translate(exc) from exc
    rows = answer.rows or []
    return {
        "answer": answer.answer,
        "sql": answer.sql,
        "columns": answer.columns,
        "rows": rows[:MAX_ROWS],
        "row_count": answer.row_count,
        "truncated": len(rows) > MAX_ROWS,
        "run_id": answer.run_id,
        # Surfaced rather than hidden: a degraded answer is one the agent could
        # not fully produce, and a model should be able to say so.
        "degraded": answer.degraded,
    }


@mcp.tool()
async def run_governed_query(sql: str) -> dict[str, Any]:
    """Run a read-only SQL SELECT against the property marts.

    Every statement passes the same guardrails the app uses: SELECT only, single
    statement, allowlisted tables, and row level security scoped to this
    connection's grants. Anything else is rejected rather than partially run.
    Use describe_schema first to get real column names.
    """
    try:
        result = await run_sql(SqlRequest(sql=sql), user=_user(), channel=CHANNEL)
    except HTTPException as exc:
        raise _translate(exc) from exc
    # The SQL editor's contract is 200-with-an-error-field, not a 4xx, so the UI
    # can render a rejection inline. Left unchecked that turns a blocked
    # "DELETE FROM ..." into an empty SUCCESS as far as a model is concerned —
    # it would conclude the table is empty rather than that it was refused.
    if result.error:
        raise BackendError(result.error)
    rows = result.rows or []
    return {
        "columns": result.columns,
        "rows": rows[:MAX_ROWS],
        "row_count": result.row_count,
        "truncated": len(rows) > MAX_ROWS,
    }


@mcp.tool()
async def get_audit(limit: int = 10) -> list[dict[str, Any]]:
    """Recent questions and queries run through this connection.

    Every call made through this surface is recorded against its service
    account; this reads that trail back. Useful for checking what a previous
    session did.
    """
    try:
        rows = await sql_history(limit=max(1, min(limit, 50)), user=_user())
    except HTTPException as exc:
        raise _translate(exc) from exc
    return [
        {
            "sql": r.get("sql"),
            "row_count": r.get("row_count"),
            "created_at": r.get("created_at"),
            "status": r.get("status"),
        }
        for r in (rows or [])
    ]


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------
class ServiceKeyGate:
    """Require a ``dpk_`` key pinned to ``surface='mcp'`` before the transport.

    Raw ASGI rather than a FastAPI dependency because the MCP app is mounted as
    an ASGI sub-application: there is no route here for ``Depends`` to hang off.
    Authenticating out here also means an anonymous request never reaches the
    JSON-RPC layer at all — it cannot open a session, cannot list tools, and
    cannot consume a connection slot.
    """

    def __init__(self, app: Starlette) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        try:
            user = await service_account_from_header(headers.get("authorization"), "mcp")
        except HTTPException as exc:
            await _json_error(send, exc.status_code, str(exc.detail))
            return
        except Exception:  # noqa: BLE001 - a DB blip must not leak a stack trace here
            log.exception("mcp authentication failed")
            await _json_error(send, 503, "Authentication backend unavailable")
            return
        token = _current_user.set(user)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_user.reset(token)


class McpPathNormalizer:
    """Make ``/mcp`` work as well as ``/mcp/``.

    Starlette compiles a ``Mount("/mcp")`` to ``^/mcp/(?P<path>.*)$``, so a bare
    ``/mcp`` misses the mount entirely and falls through to the router's
    redirect_slashes, which answers 307. Clients that follow redirects survive
    that; it still means an extra round trip on every JSON-RPC POST and a
    trailing slash every client config has to remember. Rewriting the path here
    — before routing — makes the obvious URL the working one.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") == "/mcp":
            scope = {**scope, "path": "/mcp/", "raw_path": b"/mcp/"}
        await self.app(scope, receive, send)


async def _json_error(send: Send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def build_mcp_app() -> tuple[Starlette, ServiceKeyGate]:
    """Build the mounted MCP application and its authenticating wrapper.

    Returns both because the caller needs the inner Starlette app to drive the
    session manager from its own lifespan — ``streamable_http_app()`` creates
    that manager lazily, and a mounted app's lifespan does not run on its own.
    """
    security = (
        TransportSecuritySettings(
            allowed_hosts=settings.mcp_allowed_host_list,
            allowed_origins=settings.mcp_allowed_origin_list,
        )
        if settings.mcp_allowed_hosts
        # See the module docstring: the key gate is the control, so an unset
        # allowlist means "off", not "localhost only" (which would 421 every
        # deployed request and is exactly the trap s35 shipped with).
        else TransportSecuritySettings(enable_dns_rebinding_protection=False)
    )
    inner = mcp.streamable_http_app(
        streamable_http_path="/",
        # Stateless: each JSON-RPC call is self-contained and handled in its own
        # request task. That is what makes the per-request identity ContextVar
        # correct, and none of these tools need cross-call session state.
        stateless_http=True,
        transport_security=security,
    )
    return inner, ServiceKeyGate(inner)
