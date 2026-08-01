"""Tier-1 MCP protocol conformance (s35 rung 3).

Deterministic, no LLM, safe for CI: connects a real MCP client over streamable
HTTP and asserts the tool surface is what clients expect. This is what catches a
renamed or malformed tool BEFORE a model ever sees it — the live Claude smoke
test (make mcp-smoke) is slower, costs tokens and can't gate every PR.

Skips when no server is running, so a plain `pytest` on a laptop stays green.
"""

from __future__ import annotations

import os

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

MCP_URL = os.environ.get("MCP_URL", "http://localhost:8200/mcp")

EXPECTED_TOOLS = {
    "list_datasets",
    "describe_schema",
    "ask_question",
    "run_governed_query",
    "get_audit",
}


async def _tools() -> dict[str, object]:
    async with streamable_http_client(MCP_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            return {t.name: t for t in result.tools}


@pytest.fixture(scope="module")
def _require_server() -> None:
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(MCP_URL)
    sock = socket.socket()
    sock.settimeout(1.0)
    try:
        sock.connect((parsed.hostname or "localhost", parsed.port or 80))
    except OSError:
        pytest.skip(f"no MCP server listening on {MCP_URL}")
    finally:
        sock.close()


@pytest.mark.usefixtures("_require_server")
async def test_exposes_exactly_the_expected_tools() -> None:
    # Exact-set, not a subset: an accidentally exported helper is as much a bug
    # as a missing tool, because every extra tool is context a model must read.
    assert set((await _tools()).keys()) == EXPECTED_TOOLS


@pytest.mark.usefixtures("_require_server")
async def test_every_tool_is_described_for_a_model() -> None:
    # Descriptions are not documentation here — they are the only thing the model
    # uses to choose. An undescribed tool is an unusable one.
    for name, tool in (await _tools()).items():
        description = getattr(tool, "description", None) or ""
        assert len(description) > 40, f"{name} needs a real description, got {description!r}"


@pytest.mark.usefixtures("_require_server")
async def test_tool_input_schemas_are_typed() -> None:
    tools = await _tools()
    schema = getattr(tools["run_governed_query"], "input_schema", {})
    assert schema.get("properties", {}).get("sql", {}).get("type") == "string"
    assert "sql" in schema.get("required", [])

    describe = getattr(tools["describe_schema"], "input_schema", {})
    assert "dataset_slug" in describe.get("properties", {})


@pytest.mark.usefixtures("_require_server")
async def test_list_datasets_returns_the_keys_grants() -> None:
    async with streamable_http_client(MCP_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("list_datasets", {})
    assert not result.is_error, result.content
    text = str(result.content)
    # The grants seeded for the mcp service account. If the key were unscoped or
    # missing, this comes back empty rather than "everything" — which is the
    # property worth pinning.
    assert "nsw_sales" in text


@pytest.mark.usefixtures("_require_server")
async def test_guardrails_reject_a_write() -> None:
    # The MCP surface must not be a way around sql_guardrails. A model asking to
    # delete rows has to fail, and fail as a tool error it can read.
    async with streamable_http_client(MCP_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "run_governed_query", {"sql": "DELETE FROM marts.housing"}
            )
    assert result.is_error or "error" in str(result.content).lower()
