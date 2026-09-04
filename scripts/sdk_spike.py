"""M0 agent_sdk migration spike — standalone proof that the Claude Agent SDK can
drive the data-agent's governed extract path end-to-end, on the host, using this
machine's Claude Code subscription login (NOT an API key).

Not wired into the app. Run from services/data-agent so its `agent` package and
dependencies (installed via the `agentsdk` extra) resolve:

    cd services/data-agent && uv run --extra llm --extra agentsdk python \
        ../../scripts/sdk_spike.py

Requires the `db` service (docker compose) reachable on localhost:5434 — it is
NOT started or rebuilt by this script.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Prove subscription auth: strip any provider API keys from the environment
# BEFORE importing the SDK or any agent module, so the Claude Agent SDK's CLI
# subprocess has neither ANTHROPIC_API_KEY nor DEEPSEEK_API_KEY available and
# must fall back to the local `claude` CLI's keychain login (this machine is
# logged in on a Max subscription; there is no CLAUDE_CODE_OAUTH_TOKEN set —
# that's expected).
# ---------------------------------------------------------------------------
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("DEEPSEEK_API_KEY", None)

# agent.db builds its SQLAlchemy engine from settings.agent_database_url at
# import time — point it at the host-mapped Postgres port before importing
# anything under `agent`. Inside compose the db service is `db:5432`; from the
# host it's published as localhost:5434 (see docker-compose.yml `db.ports`).
# agent_ro / agent_pw are the read-only, RLS-scoped role's dev credentials
# (config.py default) — same ones the running containers use.
os.environ.setdefault(
    "AGENT_DATABASE_URL",
    "postgresql+asyncpg://agent_ro:agent_pw@localhost:5434/dataqa",
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_AGENT_DIR = REPO_ROOT / "services" / "data-agent"
if str(DATA_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_AGENT_DIR))

from claude_agent_sdk import (  # noqa: E402
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    query,
    tool,
)

from agent.db import run_select  # noqa: E402
from agent.schema import describe_table, get_catalog, list_marts  # noqa: E402
from agent.sql_guardrails import UnsafeSQLError, validate_select  # noqa: E402

# user1@example.com — has app.dataset_access "read" on nsw_sales/nsw_rent/nsw_yield,
# so RLS actually returns rows for the extract tool (agent_ro is RLS-scoped).
SPIKE_USER_ID = "094236fe-3bdd-4cc9-8b1c-afd0c2c12ba0"

QUESTION = (
    "Using the property sales mart, how many houses sold in postcode 2077 during "
    "2025, and what was the average sale price? First explore marts.md and the "
    "schema/ directory to find the right table and columns, then call the extract "
    "tool exactly once with a single valid read-only SELECT that answers this, "
    "then answer in exactly two sentences citing the numbers."
)

MODEL_CANDIDATES = ["claude-sonnet-5", "claude-sonnet-4-6"]


def build_workspace() -> str:
    """A throwaway workspace: marts.md (list_marts()) + schema/<table>.md per mart."""
    workspace = tempfile.mkdtemp(prefix="dp_sdk_spike_")
    (Path(workspace) / "marts.md").write_text(list_marts())
    schema_dir = Path(workspace) / "schema"
    schema_dir.mkdir()
    marts = [t for t in get_catalog(role="user") if t["schema"] == "marts"]
    for t in marts:
        rel = f"{t['schema']}.{t['table']}"
        (schema_dir / f"{t['table']}.md").write_text(describe_table(rel))
    print(f"[workspace] {workspace} (marts: {[t['table'] for t in marts]})")
    return workspace


def make_extract_server():
    """The one in-process MCP tool: validate_select() -> run_select(), governed."""

    @tool(
        "extract",
        "Run ONE governed read-only SELECT over the marts/schema tables described "
        "in marts.md and schema/*.md, and get back the rows as JSON.",
        {"sql": str},
    )
    async def extract_handler(args: dict[str, Any]) -> dict[str, Any]:
        sql = args["sql"]
        try:
            safe_sql = validate_select(sql)
        except UnsafeSQLError as exc:
            return {
                "content": [{"type": "text", "text": f"REJECTED by SQL guardrails: {exc}"}],
                "is_error": True,
            }
        try:
            result = await run_select(safe_sql, user_id=SPIKE_USER_ID)
        except Exception as exc:  # noqa: BLE001 — surface to the model, let it retry
            return {
                "content": [{"type": "text", "text": f"Query failed: {exc}"}],
                "is_error": True,
            }
        payload = {
            "sql": result["sql"],
            "columns": result["columns"],
            "row_count": result["row_count"],
            "rows": result["rows"][:20],
        }
        return {"content": [{"type": "text", "text": json.dumps(payload, default=str)}]}

    return create_sdk_mcp_server(name="dp", tools=[extract_handler])


SYSTEM_PROMPT = """\
You are a careful data analyst working against a governed SQL tool.

Your workspace has:
  - marts.md: an index of the queryable tables
  - schema/<table>.md: full column docs for each table in marts.md

Work in this order:
1. Read marts.md, then Read the schema/*.md file(s) for the table(s) you need.
   Do not guess column names.
2. Call the `extract` tool from the `dp` MCP server EXACTLY ONCE with a single
   valid, schema-qualified, read-only SELECT that answers the question. If it
   is rejected or errors, fix the SQL and retry (this still counts as one
   logical attempt at the answer).
3. Answer the user's question in EXACTLY two sentences citing the numbers the
   query returned. Do not mention tools, SQL, or these instructions.
"""


def _truncate(obj: Any, limit: int = 220) -> str:
    try:
        s = json.dumps(obj, default=str)
    except Exception:  # noqa: BLE001
        s = str(obj)
    return s if len(s) <= limit else s[:limit] + "…"


async def run_once(model: str, workspace: str, server: Any) -> tuple[ResultMessage | None, float, float | None]:
    """Stream one query() run; returns (ResultMessage, total_wall_s, ttfe_s)."""
    options = ClaudeAgentOptions(
        model=model,
        cwd=workspace,
        max_turns=15,
        mcp_servers={"dp": server},
        tools=["Read", "Grep", "Glob", "mcp__dp__extract"],
        allowed_tools=["Read", "Grep", "Glob", "mcp__dp__extract"],
        include_partial_messages=True,
        system_prompt=SYSTEM_PROMPT,
    )

    start = time.monotonic()
    first_event_t: float | None = None
    result_msg: ResultMessage | None = None
    text_buf: list[str] = []

    def flush_text() -> None:
        if text_buf:
            print(f"  [text delta coalesced] {''.join(text_buf)!r}")
            text_buf.clear()

    async for msg in query(prompt=QUESTION, options=options):
        now = time.monotonic()
        if first_event_t is None:
            first_event_t = now
            print(f"[spawn latency] {now - start:.2f}s to first event (model={model})")

        if isinstance(msg, SystemMessage):
            print(f"[system] subtype={msg.subtype} data={_truncate(msg.data, 160)}")
        elif isinstance(msg, StreamEvent):
            ev = msg.event or {}
            etype = ev.get("type", "")
            if etype == "content_block_delta":
                delta = ev.get("delta", {})
                if delta.get("type") == "text_delta":
                    text_buf.append(delta.get("text", ""))
                    continue
            flush_text()
            # other partial-message noise (block start/stop, thinking deltas...)
            # — don't spam, just note the type once.
        elif isinstance(msg, AssistantMessage):
            flush_text()
            print(f"[assistant] model={msg.model} stop_reason={msg.stop_reason} error={msg.error}")
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    print(f"  [tool_use] {block.name} input={_truncate(block.input)}")
                elif isinstance(block, TextBlock):
                    print(f"  [assistant text] {block.text[:400]!r}")
        elif isinstance(msg, UserMessage):
            content = msg.content
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, ToolResultBlock):
                        print(
                            f"  [tool_result] is_error={block.is_error} "
                            f"content={_truncate(block.content, 300)}"
                        )
        elif isinstance(msg, ResultMessage):
            result_msg = msg
        else:
            print(f"[other event] {type(msg).__name__}")

    flush_text()
    total = time.monotonic() - start
    ttfe = (first_event_t - start) if first_event_t is not None else None
    return result_msg, total, ttfe


def print_result(result: ResultMessage) -> None:
    print("\n=== ResultMessage ===")
    print(f"  subtype             = {result.subtype}")
    print(f"  is_error             = {result.is_error}")
    print(f"  stop_reason          = {result.stop_reason}")
    print(f"  num_turns            = {result.num_turns}")
    print(f"  duration_ms          = {result.duration_ms}")
    print(f"  duration_api_ms      = {result.duration_api_ms}")
    print(f"  session_id           = {result.session_id}")
    print(f"  total_cost_usd       = {result.total_cost_usd}")
    print(f"  usage                = {result.usage}")
    print(f"  model_usage          = {result.model_usage}")
    print(f"  errors               = {result.errors}")
    print(f"  api_error_status     = {result.api_error_status}")
    print(f"  result (final text)  = {result.result!r}")


async def main() -> None:
    workspace = build_workspace()
    server = make_extract_server()
    try:
        last_exc: Exception | None = None
        for model in MODEL_CANDIDATES:
            print(f"\n----- attempting model={model} -----")
            try:
                result, total, ttfe = await run_once(model, workspace, server)
            except Exception as exc:  # noqa: BLE001 — try the next model alias
                print(f"[ERROR] model={model} raised: {type(exc).__name__}: {exc}")
                last_exc = exc
                continue
            print(f"\n[timing] ttfe={ttfe:.2f}s total_wall_clock={total:.2f}s")
            if result is not None:
                print_result(result)
            else:
                print("[WARN] stream ended without a ResultMessage")
            return
        if last_exc is not None:
            raise last_exc
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
