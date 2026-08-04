"""Tier-2 MCP acceptance test: a real Claude client, end to end (s35 rung 3).

Launches the `claude` CLI headless with our MCP server configured, asks a real
property question, and checks two things IN ORDER:

  1. Claude actually CALLED one of our MCP tools.
  2. The figure it reported matches a direct query against the same data.

The order matters, and (1) is the real test. Claude knows roughly what Sydney
property costs, so pointed at a dead MCP server it will happily produce a
plausible-looking answer from parametric memory — and a test that only checked
for a number in the output would go green while the server was completely
broken. Ask the question, then prove the tool was used.

Skips (exit 0) when there is no usable client, matching how the journey evals
behave, so this can sit in a Makefile without breaking a CI run that has no
credentials. Note the gate is the CLI's *presence*, not ANTHROPIC_API_KEY: the
`claude` CLI authenticates by subscription too, so keying the skip on that env
var would silently skip forever on a perfectly working machine.

Since s36 the surface is mounted on backend-api at /mcp and requires a dpk_ key
minted for surface='mcp' — pass it as MCP_SERVICE_KEY.

Usage:  MCP_SERVICE_KEY=dpk_... uv run python scripts/mcp_smoke.py
        [--url http://localhost:8000/mcp]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

QUESTION = (
    "Using the Data Pilot tools, how many properties were sold in postcode 2077? "
    "Answer with the number."
)
TOOL_PREFIX = "mcp__datapilot__"
CLI_TIMEOUT_S = 300


def _log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.environ.get("MCP_URL", "http://localhost:8000/mcp"))
    parser.add_argument(
        "--expect",
        type=int,
        default=int(os.environ.get("MCP_SMOKE_EXPECT", "0")),
        help="Ground-truth count to check the answer against (0 = skip the value check).",
    )
    args = parser.parse_args()

    if os.environ.get("MCP_SMOKE") == "0":
        _log("SKIP: MCP_SMOKE=0.")
        return 0
    if shutil.which("claude") is None:
        _log("SKIP: the `claude` CLI is not on PATH — no live client to drive.")
        return 0

    key = os.environ.get("MCP_SERVICE_KEY", "")
    if not key:
        # A missing key is a setup gap, not a broken integration: without it the
        # gate returns 401 and the run would fail for a reason that has nothing
        # to say about whether the surface works.
        _log("SKIP: MCP_SERVICE_KEY is not set — mint one for surface='mcp' in Settings.")
        return 0

    config = {
        "mcpServers": {
            "datapilot": {
                "type": "http",
                "url": args.url,
                "headers": {"Authorization": f"Bearer {key}"},
            }
        }
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(config, fh)
        config_path = fh.name

    cmd = [
        "claude",
        "-p",
        QUESTION,
        "--mcp-config",
        config_path,
        "--output-format",
        "stream-json",
        "--verbose",
        # Only our tools; no filesystem or shell access for a test that is
        # supposed to prove one specific integration works.
        "--allowed-tools",
        f"{TOOL_PREFIX}ask_question",
        f"{TOOL_PREFIX}run_governed_query",
        f"{TOOL_PREFIX}list_datasets",
    ]
    _log(f"→ asking Claude via MCP at {args.url}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=CLI_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        _log(f"FAIL: the client did not finish within {CLI_TIMEOUT_S}s.")
        return 1
    finally:
        os.unlink(config_path)

    tool_calls: list[str] = []
    answer_text: list[str] = []
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = event.get("message") or {}
        for block in message.get("content", []) or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and str(block.get("name", "")).startswith(
                TOOL_PREFIX
            ):
                tool_calls.append(str(block["name"]))
            if block.get("type") == "text":
                answer_text.append(str(block.get("text", "")))
        if event.get("type") == "result" and event.get("result"):
            answer_text.append(str(event["result"]))

    answer = "\n".join(answer_text)

    # An unauthenticated CLI is "no client available", not a broken integration.
    combined = (proc.stderr + answer).lower()
    if proc.returncode != 0 and any(
        marker in combined
        for marker in ("not logged in", "authentication", "unauthorized", "login")
    ):
        _log("SKIP: the `claude` CLI is not authenticated.")
        return 0

    # (1) The actual test.
    if not tool_calls:
        _log("FAIL: Claude answered WITHOUT calling any MCP tool.")
        _log("      That means the server was unreachable and this answer is from memory,")
        _log("      not from the data — exactly the false pass this check exists to catch.")
        _log(f"      stderr: {proc.stderr[-600:]}")
        _log(f"      answer: {answer[:400]}")
        return 1
    _log(f"✓ MCP tools invoked: {', '.join(sorted(set(tool_calls)))}")

    # (2) Only meaningful once (1) has passed.
    if args.expect:
        formatted = {f"{args.expect:,}", str(args.expect)}
        if not any(f in answer for f in formatted):
            _log(f"FAIL: expected {args.expect:,} in the answer.")
            _log(f"      answer: {answer[:400]}")
            return 1
        _log(f"✓ answer carries the ground-truth figure ({args.expect:,})")

    _log("✓ MCP smoke passed")
    _log(f"  answer: {answer.strip()[:300]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
