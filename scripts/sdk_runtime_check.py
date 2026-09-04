"""agent_sdk M1 live check — drive ``answer_with_sdk`` against the real stack.

The unit suite (``services/data-agent/tests/test_sdk_agent.py``) proves the
contract offline with a fake SDK. This proves the *runtime*: a real Claude Code
CLI subprocess, the real workspace, the real governed extract against local
Postgres, real skills in the sandbox — end to end, in-process (no HTTP), which
is what makes a failure here point at the runtime rather than the transport.

Run from services/data-agent so the `agent` package and the `agentsdk` extra
resolve:

    cd services/data-agent && uv run --extra llm --extra agentsdk python \
        ../../scripts/sdk_runtime_check.py

Requires the `db` service (docker compose) reachable on localhost:5434 and a
logged-in `claude` CLI (subscription auth) or CLAUDE_CODE_OAUTH_TOKEN. Nothing
is rebuilt or restarted.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# Subscription auth: strip provider API keys from this process BEFORE importing
# anything, so nothing can hand one to the CLI. sdk_agent.cli_env() blanks them
# for the subprocess too, but a clean parent environment makes the check honest
# about which credential actually answered.
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("DEEPSEEK_API_KEY", None)

# agent.db builds its engine from settings at import time: inside compose the DB
# is db:5432, from the host it is published on localhost:5434.
os.environ.setdefault(
    "AGENT_DATABASE_URL",
    "postgresql+asyncpg://agent_ro:agent_pw@localhost:5434/dataqa",
)
os.environ["AGENT_RUNTIME"] = "agent_sdk"

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_AGENT_DIR = REPO_ROOT / "services" / "data-agent"
if str(DATA_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_AGENT_DIR))

WORKSPACE_BASE = Path(tempfile.mkdtemp(prefix="dp_sdk_runtime_check_"))
os.environ["SDK_WORKSPACE_DIR"] = str(WORKSPACE_BASE)

from agent.config import settings  # noqa: E402
from agent.sdk_agent import answer_with_sdk  # noqa: E402

# user1@example.com — holds app.dataset_access "read" on nsw_sales/nsw_rent/
# nsw_yield, so the RLS-scoped agent_ro role actually returns rows.
CHECK_USER_ID = "094236fe-3bdd-4cc9-8b1c-afd0c2c12ba0"
QUESTION = "What is the trend in house sale prices in Hornsby over the last few years?"


def _fail(message: str) -> None:
    print(f"  FAIL  {message}")


def _ok(message: str) -> None:
    print(f"  ok    {message}")


async def main() -> int:
    print(f"[config] runtime={settings.agent_runtime} model={settings.sdk_model}")
    print(f"[config] workspace base={WORKSPACE_BASE}")
    print(f"[question] {QUESTION}\n")

    progress: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    frames: list[dict[str, Any]] = []
    started = time.monotonic()
    task = asyncio.ensure_future(
        answer_with_sdk(QUESTION, user_id=CHECK_USER_ID, plan="pro", progress=progress)
    )
    # Drain live so frame ORDER relative to the result is observed, not inferred.
    while not task.done() or not progress.empty():
        try:
            frame = await asyncio.wait_for(progress.get(), timeout=2.0)
        except TimeoutError:
            continue
        frames.append(frame)
        label = frame.get("event") or frame.get("action")
        detail = frame.get("kind") or frame.get("detail") or ""
        print(f"  [{time.monotonic() - started:6.1f}s] {label}: {detail}")
    result = await task
    wall = time.monotonic() - started

    print(f"\n[timing] {wall:.1f}s wall clock")
    if result is None:
        _fail("answer_with_sdk returned None (no answer at all)")
        return 1
    if result.get("fallback"):
        print(f"[salvage] {result}")
        _fail("run fell back to the stub instead of producing a report")
        return 1

    print(f"\n[answer] {result['answer']}\n")
    report = result.get("report") or {}
    pages = result.get("pages") or []
    page_frames = [f for f in frames if f.get("event") == "page" and f.get("status") == "complete"]
    plan_frames = [f for f in frames if f.get("event") == "plan"]

    failures = 0

    def check(condition: bool, message: str) -> None:
        nonlocal failures
        if condition:
            _ok(message)
        else:
            failures += 1
            _fail(message)

    print("[assertions]")
    check(bool(str(result.get("answer") or "").strip()), "answer text is present")
    check(result.get("engine") == "agent_sdk", f"engine == agent_sdk (got {result.get('engine')})")
    check(bool(pages), f"report carries pages ({len(pages)})")
    check(
        bool(report.get("queries")), f"governed queries recorded ({len(report.get('queries', []))})"
    )
    check(bool(result.get("sql")), "a primary SQL statement is attached")
    check(bool(result.get("row_count")), f"rows returned ({result.get('row_count')})")
    check(bool(result.get("input_tokens")), f"input_tokens={result.get('input_tokens')}")
    check(bool(result.get("output_tokens")), f"output_tokens={result.get('output_tokens')}")
    check(result.get("cost_usd") is not None, f"cost_usd={result.get('cost_usd')}")
    check(bool(plan_frames), "a plan frame was emitted")
    check(
        bool(plan_frames and frames.index(plan_frames[0]) == 0),
        "the plan frame came first (ghost slots before model work)",
    )
    check(bool(page_frames), f"page frames streamed before the result ({len(page_frames)})")
    check(
        any(s.get("kind") == "model" for s in result.get("steps", [])),
        "the trace carries model turns",
    )
    check(
        any(s.get("kind") == "decision_log" for s in result.get("steps", [])),
        "the trace carries the decision log",
    )
    leftover = (
        list((WORKSPACE_BASE / "runs").glob("*")) if (WORKSPACE_BASE / "runs").is_dir() else []
    )
    check(not leftover, f"workspace cleaned up (leftover: {leftover})")

    print(
        f"\n[usage] input={result.get('input_tokens')} output={result.get('output_tokens')} "
        f"cache_read={result.get('cache_read_tokens')} "
        f"cache_write={result.get('cache_write_tokens')} cost=${result.get('cost_usd')}"
    )
    print(f"[pages] {[p.get('kind', p.get('template')) for p in pages]}")
    print(f"[knowledge] {report.get('knowledge_pages_used')}")
    print(f"[trace] {len(result.get('steps', []))} entries")
    print(f"\n{'PASS' if failures == 0 else f'FAIL ({failures} assertion(s))'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    finally:
        shutil.rmtree(WORKSPACE_BASE, ignore_errors=True)
