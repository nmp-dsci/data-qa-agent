"""The Claude Agent SDK runtime (agent_sdk M1) — offline contract tests.

No network, no subprocess, no database: ``claude_agent_sdk`` is replaced by a
fake module whose ``query()`` is a scripted async generator that calls the real
in-process MCP tool handlers. That exercises everything this runtime actually
owns — the page-plan-before-model-work ordering, quota enforcement and its hard
stop, the trace shape ``app.query_runs.trace`` expects, usage/cost mapping, and
the AGENT_RUNTIME dispatch — against the same tool implementations the champion
runs, so a green suite means the two runtimes really do share their behaviour.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from agent import main, sandbox_agent, sdk_agent
from agent.agent_common import _build_trace
from agent.config import settings
from agent.main import AskRequest, UserCtx
from agent.sdk_trace import SdkTrace, knowledge_page_from_path

USER_ID = "00000000-0000-0000-0000-000000000001"


# ---------------------------------------------------------------------------
# A fake claude_agent_sdk: same call surface, scripted message stream
# ---------------------------------------------------------------------------


@dataclass
class TextBlock:
    text: str


@dataclass
class ThinkingBlock:
    thinking: str


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: Any = None
    is_error: bool = False


@dataclass
class AssistantMessage:
    content: list[Any]
    model: str = "claude-sonnet-5"
    usage: dict[str, Any] | None = None


@dataclass
class UserMessage:
    content: Any


@dataclass
class ResultMessage:
    subtype: str = "success"
    num_turns: int = 3
    session_id: str = "sess-1"
    is_error: bool = False
    result: str | None = None
    usage: dict[str, Any] | None = None
    model_usage: dict[str, Any] | None = None
    total_cost_usd: float | None = None


@dataclass
class _FakeTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any]


class _FakeServer:
    def __init__(self, name: str, tools: list[_FakeTool]) -> None:
        self.name = name
        self.tools = {t.name: t for t in tools}


class _FakeOptions:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


class _FakeHookMatcher:
    def __init__(self, matcher: str | None = None, hooks: list[Any] | None = None) -> None:
        self.matcher = matcher
        self.hooks = hooks or []


class FakeSdk:
    """Stands in for the ``claude_agent_sdk`` module."""

    ClaudeAgentOptions = _FakeOptions
    HookMatcher = _FakeHookMatcher

    def __init__(self, script: Callable[[str, Any], AsyncIterator[Any]]) -> None:
        self._script = script
        self.options: Any = None

    def tool(
        self, name: str, description: str, input_schema: dict[str, Any]
    ) -> Callable[[Callable[[dict[str, Any]], Any]], _FakeTool]:
        def register(handler: Callable[[dict[str, Any]], Any]) -> _FakeTool:
            return _FakeTool(name, description, input_schema, handler)

        return register

    def create_sdk_mcp_server(self, *, name: str, tools: list[_FakeTool]) -> _FakeServer:
        return _FakeServer(name, tools)

    def query(self, *, prompt: str, options: Any) -> AsyncIterator[Any]:
        self.options = options
        return self._script(prompt, options)


# ---------------------------------------------------------------------------
# Fixtures / doubles for the governed tools
# ---------------------------------------------------------------------------


@dataclass
class _FakeAnalysis:
    report: dict[str, Any] | None
    skills_used: list[str] = field(default_factory=list)
    skill_gaps: list[Any] = field(default_factory=list)
    used_inline_math: bool = False
    frames: list[dict[str, Any]] = field(default_factory=list)
    # s49 M0: the sandbox now returns whatever the model's code printed; the
    # analysis trace step (and its span) report its length.
    stdout: str = ""
    error: str | None = None


def _report() -> dict[str, Any]:
    return {
        "element_id": "report",
        "summary": "Median rent is $671/wk, up 6.1% YoY.",
        "headlines": [
            {
                "element_id": "headline:0",
                "label": "median rent",
                "value": "$671/wk",
                "basis": "6-mo rolling, 2026-05",
                "related": False,
                "query_ref": "Q1",
            }
        ],
        "insights": [],
        "profiles": [],
        "main_chart": None,
    }


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No DB, no dbt manifest, no memory store, workspaces under tmp_path."""
    monkeypatch.delenv("DBT_MANIFEST", raising=False)
    monkeypatch.setattr(settings, "sdk_workspace_dir", str(tmp_path))

    async def fake_recall(user_id: str, question: str) -> list[str]:
        return []

    async def fake_extract(sql: str, *, user_id: str) -> tuple[pd.DataFrame, dict[str, Any]]:
        result = {
            "sql": sql,
            "columns": ["month", "value"],
            "rows": [["2026-04", 1], ["2026-05", 2]],
            "row_count": 2,
        }
        return pd.DataFrame(result["rows"], columns=result["columns"]), result

    def fake_run_code(code: str, *, frames: dict[str, Any] | None = None) -> _FakeAnalysis:
        return _FakeAnalysis(report=_report(), skills_used=["trend_series"])

    monkeypatch.setattr(sdk_agent, "recall_memories", fake_recall)
    monkeypatch.setattr(sandbox_agent, "run_extract", fake_extract)
    monkeypatch.setattr(sandbox_agent, "run_code", fake_run_code)


def _run(script: Callable[[str, Any], AsyncIterator[Any]], **kwargs: Any) -> tuple[Any, FakeSdk]:
    sdk = FakeSdk(script)

    async def go() -> Any:
        return await sdk_agent.answer_with_sdk(
            kwargs.pop("question", "What is the rent trend in Hornsby?"),
            user_id=USER_ID,
            **kwargs,
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sdk_agent, "_load_sdk", lambda: sdk)
        return asyncio.run(go()), sdk


def _drain(queue: asyncio.Queue[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


def _happy_script(
    queue: asyncio.Queue[dict[str, Any]], observed: dict[str, Any]
) -> Callable[[str, Any], AsyncIterator[Any]]:
    """extract → run_analysis → a one-line confirmation, the way a real run goes."""

    async def script(prompt: str, options: Any) -> AsyncIterator[Any]:
        # Snapshot the frames already queued at the moment the model work starts:
        # the page plan must be one of them (ghost slots before any model call).
        observed["frames_before_model"] = queue.qsize()
        server = options.mcp_servers["dp"]

        yield AssistantMessage(
            content=[
                ThinkingBlock("read marts.md first"),
                ToolUseBlock("t0", "Read", {"file_path": f"{options.cwd}/marts.md"}),
            ],
            usage={"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 900},
        )
        yield UserMessage(content=[ToolResultBlock("t0", "marts...")])

        args = {"sql": "SELECT 1", "name": "df", "purpose": "rent by month"}
        yield AssistantMessage(
            content=[ToolUseBlock("t1", "mcp__dp__extract", args)],
            usage={"input_tokens": 50, "output_tokens": 30},
        )
        out = await server.tools["extract"].handler(args)
        yield UserMessage(
            content=[ToolResultBlock("t1", out["content"][0]["text"], out["is_error"])]
        )

        code = {"code": "result = skills.build_report(summary='x')"}
        yield AssistantMessage(content=[ToolUseBlock("t2", "mcp__dp__run_analysis", code)])
        out2 = await server.tools["run_analysis"].handler(code)
        yield UserMessage(
            content=[ToolResultBlock("t2", out2["content"][0]["text"], out2["is_error"])]
        )

        yield AssistantMessage(content=[TextBlock("Report ready.")])
        yield ResultMessage(
            result="Report ready.",
            usage={"input_tokens": 150, "output_tokens": 50},
            model_usage={
                "claude-sonnet-5": {
                    "inputTokens": 150,
                    "outputTokens": 50,
                    "cacheReadInputTokens": 900,
                    "cacheCreationInputTokens": 100,
                    "costUSD": 0.0123,
                }
            },
            total_cost_usd=0.0123,
        )

    return script


# ---------------------------------------------------------------------------
# 1. The page plan is declared before any model work
# ---------------------------------------------------------------------------


def test_plan_frame_precedes_every_model_event() -> None:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    observed: dict[str, Any] = {}
    out, _sdk = _run(_happy_script(queue, observed), plan="pro", progress=queue)

    assert out is not None
    # The plan frame was already on the wire when query() was first iterated.
    assert observed["frames_before_model"] >= 1
    frames = _drain(queue)
    assert frames[0]["event"] == "plan"
    assert [s["kind"] for s in frames[0]["pages"]][:1] == ["summary"]
    # ...and a real page followed it once run_analysis produced the report.
    pages = [f for f in frames if f.get("event") == "page" and f.get("status") == "complete"]
    assert pages and pages[0]["kind"] == "summary"


# ---------------------------------------------------------------------------
# 2. The answer contract matches the champion's
# ---------------------------------------------------------------------------


def test_answer_contract_and_workspace_cleanup(tmp_path: Path) -> None:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    out, sdk = _run(_happy_script(queue, {}), plan="pro", progress=queue)

    assert out is not None
    assert out["engine"] == sdk_agent.ENGINE == "agent_sdk"
    assert out["answer"] == _report()["summary"]
    assert out["report"]["queries"][0]["ref"] == "Q1"
    assert out["report"]["knowledge_version"]
    assert out["pages"], "a summary page should have been composed"
    assert out["sql"] == "SELECT 1"
    assert out["row_count"] == 2
    assert out["cost_usd"] == pytest.approx(0.0123)
    # input_tokens is normalised to TOTAL input (cache read/write are subsets),
    # the convention every other runtime and the cost tile already use.
    assert out["input_tokens"] == 150 + 900 + 100
    assert out["cache_read_tokens"] == 900

    # The CLI was pointed at a workspace that no longer exists (cleaned up).
    ws = Path(sdk.options.cwd)
    assert ws.parent == tmp_path / "runs"
    assert not ws.exists()

    # CLAUDE.md was delivered as the system prompt, not left to auto-discovery.
    assert "data-insight agent" in sdk.options.system_prompt
    assert sdk.options.max_turns == settings.agent_request_limit
    assert sdk.options.tools == sdk.options.allowed_tools == sdk_agent.ALLOWED_TOOLS


def test_frame_head_csv_is_written_into_the_workspace() -> None:
    seen: dict[str, Any] = {}

    def script(prompt: str, options: Any) -> AsyncIterator[Any]:
        async def gen() -> AsyncIterator[Any]:
            server = options.mcp_servers["dp"]
            args = {"sql": "SELECT 1", "name": "rent df"}
            yield AssistantMessage(content=[ToolUseBlock("t1", "mcp__dp__extract", args)])
            await server.tools["extract"].handler(args)
            seen["frames"] = sorted(p.name for p in (Path(options.cwd) / "frames").iterdir())
            yield ResultMessage()

        return gen()

    _run(script)
    assert "rent_df.head.csv" in seen["frames"]


# ---------------------------------------------------------------------------
# 3. Quotas: the courtesy STOP, then the hard stop that cancels the query
# ---------------------------------------------------------------------------


def test_extract_quota_returns_stop_then_hard_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "max_sql_attempts", 1)
    seen: dict[str, Any] = {}

    def script(prompt: str, options: Any) -> AsyncIterator[Any]:
        async def gen() -> AsyncIterator[Any]:
            server = options.mcp_servers["dp"]
            handler = server.tools["extract"].handler
            args = {"sql": "SELECT 1"}
            for i in range(3):
                yield AssistantMessage(content=[ToolUseBlock(f"t{i}", "mcp__dp__extract", args)])
                out = await handler(args)
                seen.setdefault("returns", []).append(out)
                yield UserMessage(
                    content=[ToolResultBlock(f"t{i}", out["content"][0]["text"], out["is_error"])]
                )
            yield AssistantMessage(content=[TextBlock("NEVER TRACED")])
            yield ResultMessage()

        return gen()

    out, _sdk = _run(script)

    first, second, third = seen["returns"]
    assert first["is_error"] is False  # the one allowed attempt
    assert second["content"][0]["text"].startswith("STOP: no extract attempts left")
    assert second["is_error"] is True
    assert third["is_error"] is True and "budget was spent" in third["content"][0]["text"]

    # The run stopped there: no report was built, so this is a salvage the caller
    # answers from the stub — with the spend and the reason preserved.
    assert out is not None and out["fallback"] is True and out["degraded"] is True
    assert any(s.get("kind") == "budget" for s in out["steps"])
    assert not any("NEVER TRACED" in str(s.get("content", "")) for s in out["steps"])


def test_run_analysis_quota_hard_stops_before_a_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "sandbox_run_attempts", 0)

    def script(prompt: str, options: Any) -> AsyncIterator[Any]:
        async def gen() -> AsyncIterator[Any]:
            server = options.mcp_servers["dp"]
            extract_args = {"sql": "SELECT 1"}
            yield AssistantMessage(content=[ToolUseBlock("t0", "mcp__dp__extract", extract_args)])
            await server.tools["extract"].handler(extract_args)
            yield UserMessage(content=[ToolResultBlock("t0", "ok")])
            code = {"code": "result = 1"}
            yield AssistantMessage(content=[ToolUseBlock("t1", "mcp__dp__run_analysis", code)])
            out = await server.tools["run_analysis"].handler(code)
            assert out["is_error"] is True
            yield UserMessage(content=[ToolResultBlock("t1", out["content"][0]["text"], True)])
            yield ResultMessage()

        return gen()

    out, _sdk = _run(script)
    assert out is not None and out["fallback"] is True


def test_token_budget_hard_stops_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agent_total_tokens_limit", 100)

    def script(prompt: str, options: Any) -> AsyncIterator[Any]:
        async def gen() -> AsyncIterator[Any]:
            yield AssistantMessage(
                content=[TextBlock("thinking")],
                usage={"input_tokens": 80, "output_tokens": 30},
            )
            yield AssistantMessage(content=[TextBlock("NEVER TRACED")])
            yield ResultMessage()

        return gen()

    out, _sdk = _run(script)

    assert out is not None and out["fallback"] is True and out["degraded"] is True
    budget_step = next(s for s in out["steps"] if s.get("kind") == "budget")
    assert "token budget exhausted" in budget_step["error"]
    assert not any("NEVER TRACED" in str(s.get("content", "")) for s in out["steps"])


def test_knowledge_hook_counts_reads_then_denies_past_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "max_knowledge_reads", 1)
    deps = sdk_agent._SdkDeps(user_id=USER_ID)
    hook = sdk_agent.make_knowledge_hook(deps)

    async def call(path: str) -> dict[str, Any]:
        return await hook(
            {"tool_name": "Read", "tool_input": {"file_path": path}}, "tu-1", {"signal": None}
        )

    first = asyncio.run(call("/ws/knowledge/domains/property-sales/overview.md"))
    repeat = asyncio.run(call("/ws/knowledge/domains/property-sales/overview.md"))
    denied = asyncio.run(call("/ws/knowledge/presentation/when-to-visualise.md"))
    unrelated = asyncio.run(call("/ws/schema/marts_property_sales.md"))

    assert first == {} and repeat == {} and unrelated == {}
    assert deps.knowledge_reads == 1
    assert deps.knowledge_pages == ["property-sales-overview"]
    decision = denied["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "knowledge read limit reached" in decision["permissionDecisionReason"]


def test_hook_denies_paths_outside_the_workspace(tmp_path: Path) -> None:
    deps = sdk_agent._SdkDeps(user_id=USER_ID, ws=tmp_path)
    hook = sdk_agent.make_knowledge_hook(deps)

    outside = asyncio.run(
        hook(
            {"tool_name": "Read", "tool_input": {"file_path": "/etc/passwd"}},
            "tu-1",
            {"signal": None},
        )
    )
    decision = outside["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "outside the run workspace" in decision["permissionDecisionReason"]

    inside = asyncio.run(
        hook(
            {"tool_name": "Read", "tool_input": {"file_path": str(tmp_path / "marts.md")}},
            "tu-2",
            {"signal": None},
        )
    )
    assert inside == {}


def test_hook_denies_directory_scoped_grep_over_knowledge(tmp_path: Path) -> None:
    (tmp_path / "knowledge").mkdir()
    deps = sdk_agent._SdkDeps(user_id=USER_ID, ws=tmp_path)
    hook = sdk_agent.make_knowledge_hook(deps)

    explicit_path = asyncio.run(
        hook(
            {
                "tool_name": "Grep",
                "tool_input": {"path": str(tmp_path / "knowledge"), "pattern": "bond"},
            },
            "tu-1",
            {"signal": None},
        )
    )
    assert explicit_path["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert deps.knowledge_denials == 1

    # No `path` argument: Grep's real default search root is cwd (ws), which
    # contains knowledge/ — that must be classified the same as an explicit
    # path="knowledge", not silently allowed.
    no_path = asyncio.run(
        hook({"tool_name": "Grep", "tool_input": {"pattern": "bond"}}, "tu-2", {"signal": None})
    )
    assert no_path["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert deps.knowledge_denials == 2

    scoped_elsewhere = asyncio.run(
        hook(
            {
                "tool_name": "Grep",
                "tool_input": {"path": str(tmp_path / "schema"), "pattern": "bond"},
            },
            "tu-3",
            {"signal": None},
        )
    )
    assert scoped_elsewhere == {}
    assert deps.knowledge_denials == 2


def test_knowledge_path_mapping_ignores_non_pages() -> None:
    # A real page maps onto the name knowledge.py gives it (frontmatter `name`,
    # which is NOT the filename) — that is what report["knowledge_pages_used"]
    # records on the champion path.
    assert (
        knowledge_page_from_path("/w/knowledge/domains/property-sales/overview.md")
        == "property-sales-overview"
    )
    assert knowledge_page_from_path("/w/knowledge/") is None
    # The index is navigation, not a page — it must not spend a read slot.
    assert knowledge_page_from_path("/w/knowledge/INDEX.md") is None
    # A Grep glob names a pattern, not a page.
    assert knowledge_page_from_path("/w/knowledge/*.md") is None
    assert knowledge_page_from_path("/w/knowledge/nope.md") is None
    assert knowledge_page_from_path("/w/schema/marts_x.md") is None
    assert knowledge_page_from_path("") is None


# ---------------------------------------------------------------------------
# 4. Trace shape + usage mapping
# ---------------------------------------------------------------------------


class _Part:
    """Minimal pydantic-ai part, to read the champion's entry keys off _build_trace."""

    def __init__(self, part_kind: str, **kw: Any) -> None:
        self.part_kind = part_kind
        self.__dict__.update(kw)


class _Msg:
    def __init__(self, kind: str, parts: list[_Part], **kw: Any) -> None:
        self.kind = kind
        self.parts = parts
        self.__dict__.update(kw)


def _champion_keys() -> dict[str, set[str]]:
    class _Usage:
        input_tokens = 1
        output_tokens = 2
        total_tokens = 3
        cache_read_tokens = 4
        cache_write_tokens = 5

    messages = [
        _Msg(
            "request",
            [
                _Part("system-prompt", content="sys"),
                _Part("user-prompt", content="q"),
            ],
        ),
        _Msg(
            "response",
            [
                _Part("text", content="hi"),
                _Part("tool-call", tool_name="extract", args="{}", tool_call_id="c1"),
            ],
            usage=_Usage(),
            model_name="m",
        ),
        _Msg(
            "request", [_Part("tool-return", tool_name="extract", tool_call_id="c1", content="r")]
        ),
    ]
    return {e["kind"]: set(e) for e in _build_trace(messages)}


def test_trace_entries_match_the_champion_shape() -> None:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    out, _sdk = _run(_happy_script(queue, {}), progress=queue)

    assert out is not None
    by_kind: dict[str, dict[str, Any]] = {}
    for entry in out["steps"]:
        by_kind.setdefault(str(entry.get("kind")), entry)

    champion = _champion_keys()
    assert set(by_kind["system"]) == champion["system"]
    assert set(by_kind["user"]) == champion["user"]
    assert set(by_kind["model"]) == champion["model"]
    # tool_return may additionally carry `error` when the SDK flagged one.
    assert champion["tool_return"] <= set(by_kind["tool_return"])

    model_entry = by_kind["model"]
    assert model_entry["tool_calls"][0]["name"] == "Read"
    assert model_entry["thinking"] == "read marts.md first"
    assert by_kind["tool_return"]["name"] == "Read"
    # The champion's decision log rides along unchanged.
    assert any(e.get("kind") == "decision_log" for e in out["steps"])


def test_denied_knowledge_read_is_not_recorded_as_used() -> None:
    trace = SdkTrace(system_prompt="sys", question="q")
    page_path = "/ws/knowledge/domains/property-sales/overview.md"

    trace.consume(AssistantMessage(content=[ToolUseBlock("t0", "Read", {"file_path": page_path})]))
    trace.consume(UserMessage(content=[ToolResultBlock("t0", "STOP: denied", True)]))
    assert trace.knowledge_pages == []

    trace.consume(AssistantMessage(content=[ToolUseBlock("t1", "Read", {"file_path": page_path})]))
    trace.consume(UserMessage(content=[ToolResultBlock("t1", "page content", False)]))
    assert trace.knowledge_pages == ["property-sales-overview"]


def test_usage_totals_prefer_the_result_message() -> None:
    trace = SdkTrace(system_prompt="sys", question="q")
    trace.consume(
        AssistantMessage(
            content=[TextBlock("hi")],
            usage={"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 1},
        )
    )
    trace.consume(
        ResultMessage(
            model_usage={
                "claude-sonnet-5": {
                    "inputTokens": 1000,
                    "outputTokens": 200,
                    "cacheReadInputTokens": 5000,
                    "cacheCreationInputTokens": 300,
                    "costUSD": 0.5,
                }
            },
            total_cost_usd=0.42,
            result="done",
        )
    )
    totals = trace.usage_totals("claude-sonnet-5")
    assert totals == {
        "input_tokens": 1000 + 5000 + 300,
        "output_tokens": 200,
        "cache_read_tokens": 5000,
        "cache_write_tokens": 300,
        "cost_usd": 0.42,
    }
    assert trace.final_text == "done"


def test_usage_totals_fall_back_to_the_per_turn_entries() -> None:
    trace = SdkTrace(system_prompt="sys", question="q")
    trace.consume(
        AssistantMessage(
            content=[TextBlock("hi")],
            usage={
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_input_tokens": 100,
                "cache_creation_input_tokens": 7,
            },
        )
    )
    totals = trace.usage_totals("claude-sonnet-5")
    assert totals["input_tokens"] == 117
    assert totals["output_tokens"] == 5
    assert totals["cache_read_tokens"] == 100
    assert totals["cost_usd"] is not None  # priced locally when the CLI reported none


# ---------------------------------------------------------------------------
# 5. Dispatch
# ---------------------------------------------------------------------------


def _body() -> AskRequest:
    return AskRequest(question="How many suburbs?", user=UserCtx(id=USER_ID, role="user"))


def test_dispatch_selects_the_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_sandbox(*args: Any, **kwargs: Any) -> None:
        calls.append("pydantic_ai")

    async def fake_sdk(*args: Any, **kwargs: Any) -> None:
        calls.append("agent_sdk")

    monkeypatch.setattr(main, "answer_with_sandbox", fake_sandbox)
    monkeypatch.setattr(sdk_agent, "answer_with_sdk", fake_sdk)

    monkeypatch.setattr(settings, "agent_runtime", "pydantic_ai")
    asyncio.run(main._run_agent(_body(), user_id=USER_ID, progress=None))
    monkeypatch.setattr(settings, "agent_runtime", "agent_sdk")
    asyncio.run(main._run_agent(_body(), user_id=USER_ID, progress=None))

    assert calls == ["pydantic_ai", "agent_sdk"]


def test_llm_stub_wins_over_agent_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("no runtime may run under LLM_STUB")

    async def fake_run_select(sql: str, *, user_id: str) -> dict[str, Any]:
        return {"sql": sql, "columns": ["c"], "rows": [[1]], "row_count": 1}

    monkeypatch.setattr(sdk_agent, "answer_with_sdk", boom)
    monkeypatch.setattr(main, "answer_with_sandbox", boom)
    monkeypatch.setattr(main, "run_select", fake_run_select)
    monkeypatch.setattr(settings, "agent_runtime", "agent_sdk")
    monkeypatch.setattr(settings, "llm_stub", True)
    monkeypatch.setattr(settings, "stub_latency_s", 0.0)

    out = asyncio.run(main._answer(_body()))
    assert out.engine == "stub"


def test_missing_extra_raises_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(name: str) -> Any:
        raise ImportError("No module named 'claude_agent_sdk'")

    monkeypatch.setattr(sdk_agent.importlib, "import_module", boom)
    with pytest.raises(sdk_agent.AgentSdkUnavailable) as excinfo:
        sdk_agent._load_sdk()
    assert "--extra agentsdk" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 6. Auth env: no provider key may reach the CLI subprocess
# ---------------------------------------------------------------------------


def test_cli_env_blanks_provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "claude_code_oauth_token", None)
    env = sdk_agent.cli_env()
    assert env["ANTHROPIC_API_KEY"] == ""
    assert env["DEEPSEEK_API_KEY"] == ""
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env

    monkeypatch.setattr(settings, "claude_code_oauth_token", "sk-oauth")
    assert sdk_agent.cli_env()["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-oauth"


def test_no_answer_produces_the_honest_envelope() -> None:
    def script(prompt: str, options: Any) -> AsyncIterator[Any]:
        async def gen() -> AsyncIterator[Any]:
            server = options.mcp_servers["dp"]
            args = {"reason": "the marts hold no weather data"}
            yield AssistantMessage(content=[ToolUseBlock("t1", "mcp__dp__no_answer", args)])
            await server.tools["no_answer"].handler(args)
            yield UserMessage(content=[ToolResultBlock("t1", "recorded")])
            yield ResultMessage(result="ok")

        return gen()

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    out, _sdk = _run(script, plan="pro", progress=queue)

    assert out is not None
    assert out["report"]["no_answer"] is True
    assert out["answer"] == "the marts hold no weather data"
    assert out["engine"] == "agent_sdk"
    # Every planned page slot is explicitly skipped so the client's ghosts clear.
    skipped = [f for f in _drain(queue) if f.get("status") == "skipped"]
    assert skipped


def test_a_failure_after_the_report_still_delivers_it() -> None:
    """A transport error on the confirmation turn must not throw the run away."""

    def script(prompt: str, options: Any) -> AsyncIterator[Any]:
        async def gen() -> AsyncIterator[Any]:
            server = options.mcp_servers["dp"]
            extract_args = {"sql": "SELECT 1"}
            yield AssistantMessage(content=[ToolUseBlock("t0", "mcp__dp__extract", extract_args)])
            await server.tools["extract"].handler(extract_args)
            yield UserMessage(content=[ToolResultBlock("t0", "ok")])
            code = {"code": "result = skills.build_report(summary='x')"}
            yield AssistantMessage(content=[ToolUseBlock("t1", "mcp__dp__run_analysis", code)])
            await server.tools["run_analysis"].handler(code)
            yield UserMessage(content=[ToolResultBlock("t1", "built")])
            raise RuntimeError("CLI transport dropped")

        return gen()

    out, _sdk = _run(script)
    assert out is not None
    assert not out.get("fallback")
    assert out["answer"] == _report()["summary"]
    assert out["pages"]


# ---------------------------------------------------------------------------
# 7. OTel span per run (s44 M3b)
# ---------------------------------------------------------------------------


class _FakeSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, Any] = {}

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def record_exception(self, exc: BaseException) -> None:
        self.attributes["_exception"] = str(exc)


class _FakeTracer:
    def __init__(self) -> None:
        self.spans: list[tuple[str, _FakeSpan]] = []

    @contextlib.contextmanager
    def start_as_current_span(self, name: str, context: Any = None) -> Iterator[_FakeSpan]:
        # ``context`` mirrors the real tracer's signature: s49 M0's child spans
        # pass the run span's context explicitly (the SDK invokes tool handlers
        # and hooks from tasks this module never created, so the ambient
        # contextvar is not a reliable parent).
        span = _FakeSpan()
        self.spans.append((name, span))
        yield span

    def named(self, name: str) -> list[_FakeSpan]:
        return [span for span_name, span in self.spans if span_name == name]


@pytest.fixture
def _fake_otel(monkeypatch: pytest.MonkeyPatch) -> _FakeTracer:
    """Route agent_span's tracer to a fake one, and pin the live-ordinals
    attribute so these tests don't depend on a reachable DB."""
    from opentelemetry import trace as ot_trace

    tracer = _FakeTracer()
    monkeypatch.setenv("OTLP_ENDPOINT", "http://localhost:5500")
    monkeypatch.setattr(ot_trace, "get_tracer", lambda name: tracer)  # noqa: ARG005

    async def fake_ordinals_hash() -> str:
        return "ord-fixture"

    monkeypatch.setattr(sdk_agent, "ordinals_snapshot_hash", fake_ordinals_hash)
    return tracer


def test_span_attributes_on_a_successful_run(_fake_otel: _FakeTracer) -> None:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    out, _sdk = _run(_happy_script(queue, {}), plan="pro", progress=queue)

    assert out is not None
    # The run span is opened first; s49 M0's child spans follow inside it.
    name, span = _fake_otel.spans[0]
    assert name == "agent_sdk.answer"

    # Creation-time attributes.
    assert span.attributes["question_length"] == len("What is the rent trend in Hornsby?")
    assert span.attributes["run_id"]
    assert span.attributes["model"] == settings.sdk_model
    fp = sdk_agent.build_sdk_fingerprint()
    assert span.attributes["agent_version_fingerprint"] == fp["fingerprint"]

    # The live ordinals diagnostic attribute, set best-effort mid-run.
    assert span.attributes["ordinals_snapshot_hash"] == "ord-fixture"

    # Outcome attributes, only known once the run finished.
    assert span.attributes["ok"] is True
    assert span.attributes["aborted"] is False
    assert span.attributes["num_turns"] == 3  # ResultMessage's default in _happy_script
    assert span.attributes["pages_emitted"] == 1
    assert span.attributes["session_id"] == "sess-1"
    assert span.attributes["cost_usd"] == pytest.approx(out["cost_usd"])
    assert span.attributes["input_tokens"] == out["input_tokens"]
    assert span.attributes["output_tokens"] == out["output_tokens"]
    assert span.attributes["cache_read_tokens"] == out["cache_read_tokens"]


def test_span_marks_aborted_and_not_ok_on_a_budget_hard_stop(
    _fake_otel: _FakeTracer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "sandbox_run_attempts", 0)

    def script(prompt: str, options: Any) -> AsyncIterator[Any]:
        async def gen() -> AsyncIterator[Any]:
            server = options.mcp_servers["dp"]
            extract_args = {"sql": "SELECT 1"}
            yield AssistantMessage(content=[ToolUseBlock("t0", "mcp__dp__extract", extract_args)])
            await server.tools["extract"].handler(extract_args)
            yield UserMessage(content=[ToolResultBlock("t0", "ok")])
            code = {"code": "result = 1"}
            yield AssistantMessage(content=[ToolUseBlock("t1", "mcp__dp__run_analysis", code)])
            await server.tools["run_analysis"].handler(code)
            yield UserMessage(content=[ToolResultBlock("t1", "budget spent", True)])
            yield ResultMessage()

        return gen()

    out, _sdk = _run(script)
    assert out is not None and out["fallback"] is True

    name, span = _fake_otel.spans[0]
    assert span.attributes["ok"] is False
    assert span.attributes["aborted"] is True


def test_agent_span_is_a_no_op_without_otlp_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default test environment has no OTLP_ENDPOINT set — confirms a real
    run never even looks up a tracer in that (the common) case."""
    monkeypatch.delenv("OTLP_ENDPOINT", raising=False)

    from opentelemetry import trace as ot_trace

    def boom(name: str) -> Any:
        raise AssertionError("get_tracer must not be called when OTLP_ENDPOINT is unset")

    monkeypatch.setattr(ot_trace, "get_tracer", boom)

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    out, _sdk = _run(_happy_script(queue, {}), progress=queue)
    assert out is not None


def test_child_spans_describe_every_step_of_a_run(_fake_otel: _FakeTracer) -> None:
    """s49 M0: the run is a waterfall, not one opaque span.

    Every translated step gets a child span carrying the same facts the flat
    trace entry carries — so "which stage was slow / wrong?" is answerable from
    the trace viewer alone, without joining back to app.query_runs.
    """
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    out, _sdk = _run(_happy_script(queue, {}), plan="pro", progress=queue)
    assert out is not None

    names = [name for name, _ in _fake_otel.spans]
    assert names[0] == "agent_sdk.answer"  # the run span is opened first
    assert names.count("model.turn") == 4  # one per assistant message
    assert names.count("tool.extract") == 1
    assert names.count("tool.run_analysis") == 1

    extract = _fake_otel.named("tool.extract")[0].attributes
    assert extract["status"] == "success"
    assert extract["sql"] == "SELECT 1"
    assert extract["frame"] == "df"
    assert extract["row_count"] == 2  # the fixture extract's row count
    assert extract["ms"] >= 0

    analysis = _fake_otel.named("tool.run_analysis")[0].attributes
    assert analysis["status"] == "ok"
    assert analysis["runtime"] == settings.sandbox_runtime
    assert len(analysis["code_sha"]) == 12
    assert analysis["skills_used"] == "trend_series"
    assert analysis["skill_gaps"] == 0
    assert analysis["stdout_len"] == 0
    assert "error" not in analysis  # None attributes are dropped, not stringified


def test_a_denied_knowledge_read_is_recorded_on_its_span(_fake_otel: _FakeTracer) -> None:
    """A run that spent its turns being denied looks identical to one that never
    asked, unless the denials are on the trace."""
    deps = sdk_agent._SdkDeps(user_id="u1", otel_context=None)  # noqa: SLF001
    deps.knowledge_reads = settings.max_knowledge_reads
    hook = sdk_agent.make_knowledge_hook(deps)

    async def go() -> Any:
        return await hook(
            {
                "tool_name": "Read",
                "tool_input": {"file_path": "/ws/knowledge/domains/property-sales/overview.md"},
            },
            None,
            None,
        )

    decision = asyncio.run(go())

    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"
    span = _fake_otel.named("tool.Read")[0].attributes
    assert span["denied"] is True
    assert span["quota_left"] == 0
    assert span["path"] == "/ws/knowledge/domains/property-sales/overview.md"
