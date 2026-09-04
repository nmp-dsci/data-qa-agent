"""The Claude Agent SDK runtime — the challenger beside the pydantic-ai champion.

Same job, same output contract, different loop. Where ``sandbox_agent.py`` runs a
pydantic-ai ReAct loop that pins the mart index and the knowledge index into the
system prompt and exposes ``search_knowledge``/``read_knowledge``/
``describe_table`` as tools, this runtime:

  * builds a real per-run **workspace** (``workspace.py``) — CLAUDE.md, marts.md,
    ``schema/<schema>_<table>.md``, a copy of the knowledge tree, and a
    ``frames/`` directory — and lets the model explore it with Read/Grep/Glob;
  * exposes the governed tools (``extract``, ``run_analysis``, ``lookup_values``,
    ``no_answer``, ``remember``) over an **in-process MCP server** named ``dp``;
  * drives it with ``claude_agent_sdk.query()`` against the Claude Code CLI,
    which authenticates with a subscription/OAuth login rather than an API key.

Everything downstream is identical on purpose. The page plan is declared before
any model work (ghost slots), pages stream from the same ``compose_*`` calls,
the trace is the same flat shape ``app.query_runs.trace`` already stores, and
the tool bodies are literally the same coroutines the champion calls
(``sandbox_agent._do_*``) — so an A/B on the eval pack measures the runtime, not
a rewritten prompt or a re-implemented tool.

Selected by ``AGENT_RUNTIME=agent_sdk``; ``pydantic_ai`` stays the default. The
``claude_agent_sdk`` import is lazy so the service still boots without the
optional ``agentsdk`` extra installed.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import settings
from .knowledge import knowledge_version
from .memory import recall_memories
from .pages import compose_pages, page_plan, planned_kinds
from .report import select_primary_query
from .sandbox_agent import (
    SandboxBudgetExhausted,
    _do_extract,
    _do_lookup_values,
    _do_no_answer,
    _do_remember,
    _do_run_analysis,
    _merge_decision_log,
    _query_list,
    _SbDeps,
)
from .sdk_trace import SdkTrace, knowledge_page_from_path
from .workspace import workspace

# Recorded as AgentAnswer.engine, the way the stub records "stub" and the demo
# replayer records "demo_replay" — so a run's runtime is visible in
# app.query_runs without inferring it from the model name.
ENGINE = "agent_sdk"

MCP_SERVER = "dp"
BUILTIN_TOOLS = ["Read", "Grep", "Glob"]
GOVERNED_TOOLS = ["extract", "run_analysis", "lookup_values", "no_answer", "remember"]
ALLOWED_TOOLS = [*BUILTIN_TOOLS, *(f"mcp__{MCP_SERVER}__{t}" for t in GOVERNED_TOOLS)]

# How many rows of each extracted frame are mirrored into frames/<name>.head.csv
# for the model to Read. Enough to see the shape and the value spellings without
# handing it a second copy of the data it already has in the tool return.
FRAME_HEAD_ROWS = 20

_UNSAFE_FRAME_CHARS = re.compile(r"[^A-Za-z0-9_.-]")


class AgentSdkUnavailable(RuntimeError):
    """AGENT_RUNTIME=agent_sdk without the `agentsdk` extra installed."""


def _load_sdk() -> Any:
    """The ``claude_agent_sdk`` module, or a clear error naming the missing extra.

    Imported through ``importlib`` rather than a plain ``import`` statement on
    purpose: the package only exists behind the optional ``agentsdk`` extra, and
    this way the module type-checks identically whether or not it is installed
    (CI installs only ``--extra llm``).
    """
    try:
        return importlib.import_module("claude_agent_sdk")
    except ImportError as exc:  # pragma: no cover - config error, not a code path
        raise AgentSdkUnavailable(
            "AGENT_RUNTIME=agent_sdk needs the claude-agent-sdk package: "
            "`uv sync --extra llm --extra agentsdk` (and the `claude` CLI on PATH). "
            "Set AGENT_RUNTIME=pydantic_ai to use the default runtime."
        ) from exc


# ---------------------------------------------------------------------------
# Run state
# ---------------------------------------------------------------------------


@dataclass
class _SdkDeps(_SbDeps):
    """The champion's run state plus the workspace this runtime writes into.

    Subclassing ``_SbDeps`` rather than re-declaring it is deliberate: page
    emission, quota counters, the decision log and the ghost-slot bookkeeping
    are then the *same* code on both runtimes, so a page that streams on one
    streams identically on the other.
    """

    ws: Path | None = None
    # Set when a tool blows its budget past the courtesy STOP. The message loop
    # checks it after every message and closes the query — the SDK has no
    # exception channel back into the model's loop, so this is how the
    # champion's SandboxBudgetExhausted hard stop is reproduced.
    abort_reason: str | None = None
    knowledge_denials: int = 0
    hook_events: list[dict[str, Any]] = field(default_factory=list)

    def after_frame(self, name: str, frame: Any) -> None:
        """Mirror a head sample of the extracted frame into ``frames/``.

        Best-effort: the frame is already in ``self.frames`` for run_analysis, so
        a filesystem hiccup here must never fail the extract. Purely an
        affordance for the model to eyeball what it pulled.
        """
        if self.ws is None:
            return
        safe = _UNSAFE_FRAME_CHARS.sub("_", name) or "frame"
        try:
            frames_dir = self.ws / "frames"
            frames_dir.mkdir(parents=True, exist_ok=True)
            frame.head(FRAME_HEAD_ROWS).to_csv(frames_dir / f"{safe}.head.csv", index=False)
        except Exception as exc:  # noqa: BLE001 — a sample file is never load-bearing
            print(f"[data-agent] frames/{safe}.head.csv not written: {exc}")


# ---------------------------------------------------------------------------
# MCP tool surface (1:1 with the champion's tools)
# ---------------------------------------------------------------------------


def _text(text: str, *, is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "is_error": is_error}


def _schema(properties: dict[str, str], required: list[str]) -> dict[str, Any]:
    """A literal JSON Schema, because the @tool dict form makes every key required.

    The champion's tools have optional ``name``/``purpose``/``why`` arguments and
    the model must be able to omit them, so the schema is spelled out here.
    """
    return {
        "type": "object",
        "properties": {k: {"type": v} for k, v in properties.items()},
        "required": required,
    }


TOOL_DESCRIPTIONS = {
    "extract": "Run a governed SELECT; the result is loaded as a pandas DataFrame `name`.",
    "run_analysis": (
        "Execute pandas over the extracted frame(s) in the sandbox; calls skills.*.\n\n"
        "Assign the finished report to `result` (skills.build_report(...)). Returns "
        "the skills used on success, or the error to fix. Prefer skills; if none "
        "fits you MAY use pandas but MUST call skills.skill_gap(need, why)."
    ),
    "lookup_values": (
        "Resolve exact distinct values of a text column (e.g. a name's casing). FREE.\n\n"
        "Pass the schema-qualified table the column lives in (see the mart index / "
        "schema/<table>.md). Dataset-agnostic \u2014 no default table. Resolve SEVERAL "
        'values in ONE call with `|` alternation (e.g. pattern="Normanhurst|Hornsby"); '
        "matching is case-insensitive contains, so plain words work \u2014 never retry "
        "with different casing."
    ),
    "no_answer": (
        "Declare that the available marts can't answer this question.\n\n"
        "Use when no dataset covers what's asked (wrong domain, missing metric or "
        "dimension). Records an honest reason instead of forcing a misleading "
        "report. Give a short, user-facing reason (what's missing / what the data "
        "does cover). Then return a one-line confirmation."
    ),
    "remember": "Store a durable user preference about how they want answers.",
}

TOOL_SCHEMAS = {
    "extract": _schema(
        {"sql": "string", "name": "string", "purpose": "string", "why": "string"},
        required=["sql"],
    ),
    "run_analysis": _schema({"code": "string", "why": "string"}, required=["code"]),
    "lookup_values": _schema(
        {"column": "string", "pattern": "string", "table": "string", "why": "string"},
        required=["column", "pattern", "table"],
    ),
    "no_answer": _schema({"reason": "string", "why": "string"}, required=["reason"]),
    "remember": _schema({"fact": "string"}, required=["fact"]),
}


def build_tool_server(sdk: Any, deps: _SdkDeps, *, max_extracts: int, max_runs: int) -> Any:
    """The in-process ``dp`` MCP server wrapping the shared tool implementations.

    Descriptions mirror the champion's tool docstrings verbatim \u2014 the tool
    surface the model sees is part of the behaviour under test, so it must not
    drift between the two runtimes. The handlers are registered by *calling*
    ``sdk.tool(...)`` rather than decorating, so this module keeps concrete
    annotations under a dynamically imported (``Any``-typed) SDK.
    """

    def budget_stop(exc: SandboxBudgetExhausted) -> dict[str, Any]:
        deps.abort_reason = str(exc)
        return _text(f"STOP: {exc}", is_error=True)

    async def extract_tool(args: dict[str, Any]) -> dict[str, Any]:
        try:
            out = await _do_extract(
                deps,
                str(args.get("sql") or ""),
                str(args.get("name") or "df"),
                str(args.get("purpose") or ""),
                str(args.get("why") or ""),
                max_extracts=max_extracts,
            )
        except SandboxBudgetExhausted as exc:
            return budget_stop(exc)
        return _text(out, is_error=out.startswith("STOP:"))

    async def run_analysis_tool(args: dict[str, Any]) -> dict[str, Any]:
        try:
            out = await _do_run_analysis(
                deps,
                str(args.get("code") or ""),
                str(args.get("why") or ""),
                max_runs=max_runs,
            )
        except SandboxBudgetExhausted as exc:
            return budget_stop(exc)
        return _text(out, is_error=out.startswith("STOP:"))

    async def lookup_values_tool(args: dict[str, Any]) -> dict[str, Any]:
        out = await _do_lookup_values(
            deps,
            str(args.get("column") or ""),
            str(args.get("pattern") or ""),
            str(args.get("table") or ""),
            str(args.get("why") or ""),
        )
        return _text(out)

    async def no_answer_tool(args: dict[str, Any]) -> dict[str, Any]:
        out = await _do_no_answer(deps, str(args.get("reason") or ""), str(args.get("why") or ""))
        return _text(out)

    async def remember_tool(args: dict[str, Any]) -> dict[str, Any]:
        out = await _do_remember(deps, str(args.get("fact") or ""))
        return _text(out)

    handlers = {
        "extract": extract_tool,
        "run_analysis": run_analysis_tool,
        "lookup_values": lookup_values_tool,
        "no_answer": no_answer_tool,
        "remember": remember_tool,
    }
    tools = [
        sdk.tool(name, TOOL_DESCRIPTIONS[name], TOOL_SCHEMAS[name])(handlers[name])
        for name in GOVERNED_TOOLS
    ]
    return sdk.create_sdk_mcp_server(name=MCP_SERVER, tools=tools)


# ---------------------------------------------------------------------------
# Knowledge-read quota (the champion's max_knowledge_reads, enforced on Read/Grep)
# ---------------------------------------------------------------------------


def _knowledge_target(tool_input: Any) -> str | None:
    if not isinstance(tool_input, dict):
        return None
    for key in ("file_path", "path", "pattern", "notebook_path"):
        page = knowledge_page_from_path(str(tool_input.get(key) or ""))
        if page:
            return page
    return None


def make_knowledge_hook(deps: _SdkDeps) -> Any:
    """PreToolUse hook enforcing ``max_knowledge_reads`` on Read/Grep of knowledge/.

    The champion enforces the cap inside its ``read_knowledge`` tool. Here the
    model opens pages with the built-in file tools, so the cap has to live where
    the CLI asks permission — an in-process PreToolUse hook. Re-reading a page
    already loaded is free (it is already in context), matching the champion's
    "(already loaded …)" short-circuit; a genuinely new page past the cap is
    denied with the champion's wording.
    """

    async def hook(input_data: Any, tool_use_id: str | None, context: Any) -> dict[str, Any]:
        data = input_data if isinstance(input_data, dict) else {}
        if data.get("tool_name") not in BUILTIN_TOOLS:
            return {}
        page = _knowledge_target(data.get("tool_input"))
        if page is None or page in deps.knowledge_pages:
            return {}
        if deps.knowledge_reads >= settings.max_knowledge_reads:
            deps.knowledge_denials += 1
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "knowledge read limit reached; proceed with the pages you have."
                    ),
                }
            }
        deps.knowledge_pages.append(page)
        deps.knowledge_reads += 1
        deps.steps.append({"kind": "knowledge", "status": "read", "name": page, "why": ""})
        deps.emit("Reading knowledge", page)
        return {}

    return hook


# ---------------------------------------------------------------------------
# CLI subprocess environment
# ---------------------------------------------------------------------------


def cli_env() -> dict[str, str]:
    """The env overrides handed to the Claude Code CLI subprocess.

    ``ClaudeAgentOptions.env`` is MERGED over the inherited process environment
    by the SDK's transport — it cannot delete a key — so provider keys are
    blanked rather than removed. That matters: an ``ANTHROPIC_API_KEY`` visible
    to the CLI silently switches it from the subscription/OAuth login to
    per-token API billing, and this service's own environment carries one for
    the champion runtime. An empty value reads as absent to the CLI.

    ``CLAUDE_CODE_OAUTH_TOKEN`` is the container story (no keychain in App
    Runner); on a developer host it is unset and the CLI's own login is used.
    """
    env = {
        "ANTHROPIC_API_KEY": "",
        "ANTHROPIC_AUTH_TOKEN": "",
        "DEEPSEEK_API_KEY": "",
    }
    if settings.claude_code_oauth_token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = settings.claude_code_oauth_token
    return env


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def _memories_block(recalled: list[str]) -> str:
    """The champion's wording, so the two prompts differ only by structure."""
    return "\n".join(f"- {m}" for m in recalled) if recalled else "(none stored yet)"


async def answer_with_sdk(
    question: str,
    *,
    user_id: str,
    plan: str = "free",
    progress: asyncio.Queue[dict[str, Any]] | None = None,
    run_id: str | None = None,
) -> dict[str, Any] | None:
    """Answer one question on the Agent SDK runtime.

    Mirrors ``sandbox_agent.answer_with_sandbox``: the same arguments, the same
    return contract — a full answer dict, a ``no_answer`` report, or a salvage
    dict (``fallback=True``) carrying the trace and token spend of a run that
    never produced a report so the caller can fall back to the stub without
    losing what the model actually did.
    """
    sdk = _load_sdk()
    deps = _SdkDeps(user_id=user_id, progress=progress, user_plan=plan)
    trace: SdkTrace | None = None
    try:
        # The page plan is deterministic policy per user (s10): declared and
        # emitted BEFORE any model work, so the frontend draws its ghost slots
        # while the CLI is still spawning.
        plan_slots = page_plan(plan=plan)
        deps.page_indexes = {s["kind"]: s["index"] for s in plan_slots if s["status"] != "locked"}
        deps.emit_frame("plan", {"pages": plan_slots})

        recalled = await recall_memories(user_id, question)
        include_insights = "insights" in deps.page_indexes
        base_dir = Path(settings.sdk_workspace_dir) if settings.sdk_workspace_dir else None
        async with workspace(
            run_id or uuid.uuid4().hex,
            question,
            include_insights=include_insights,
            memories_block=_memories_block(recalled),
            base_dir=base_dir,
        ) as ws:
            deps.ws = ws
            # CLAUDE.md is passed as the system prompt rather than relied on
            # being auto-read: the SDK only discovers a cwd CLAUDE.md when
            # `setting_sources` includes "project", which would also drag the
            # host's user/project settings into a server run. The file stays in
            # the workspace so Read/Grep still see it.
            system_prompt = (ws / "CLAUDE.md").read_text(encoding="utf-8")
            trace = SdkTrace(system_prompt=system_prompt, question=question)
            server = build_tool_server(
                sdk,
                deps,
                max_extracts=settings.max_sql_attempts,
                max_runs=settings.sandbox_run_attempts,
            )
            options = sdk.ClaudeAgentOptions(
                model=settings.sdk_model,
                cwd=str(ws),
                system_prompt=system_prompt,
                max_turns=settings.agent_request_limit,
                mcp_servers={MCP_SERVER: server},
                # `tools` restricts the built-in toolset (no Bash/Write/Edit in
                # the workspace); `allowed_tools` auto-approves what is left, so
                # a headless run never blocks on a permission prompt.
                tools=list(ALLOWED_TOOLS),
                allowed_tools=list(ALLOWED_TOOLS),
                hooks={"PreToolUse": [sdk.HookMatcher(hooks=[make_knowledge_hook(deps)])]},
                env=cli_env(),
            )
            await _drive(sdk, options, question, deps, trace)

        return _assemble(deps, trace, question, plan)
    except AgentSdkUnavailable:
        raise  # a misconfiguration, not a runtime failure — never hide it
    except Exception as exc:  # noqa: BLE001 — never let this path break the app
        if trace is None:
            print(f"[data-agent] agent_sdk path unavailable, using stub: {exc}")
            return None
        if deps.report is not None or deps.no_answer:
            # The champion salvages the same way: a failure AFTER the report was
            # built (a dropped transport on the confirmation turn, say) must
            # still deliver the report the user already watched stream in.
            print(f"[data-agent] agent_sdk run errored ({exc}); using result built so far")
            with contextlib.suppress(Exception):
                return _assemble(deps, trace, question, plan)
        print(f"[data-agent] agent_sdk path unavailable, using stub: {exc}")
        return _salvage(deps, trace, str(exc))


async def _drive(
    sdk: Any,
    options: Any,
    question: str,
    deps: _SdkDeps,
    trace: SdkTrace,
) -> None:
    """Stream one ``query()`` run into the trace, stopping on a budget hard-stop.

    ``aclosing`` is what turns the ``break`` into a real cancellation: closing
    the async generator tears down the CLI transport, which is this runtime's
    equivalent of the champion raising ``SandboxBudgetExhausted`` out of its
    tool and ending the run instead of letting a looping model burn the request
    budget.
    """
    stream = sdk.query(prompt=question, options=options)
    async with contextlib.aclosing(stream) as messages:
        async for msg in messages:
            trace.consume(msg)
            if deps.abort_reason:
                # Recorded on the trace itself, not in deps.steps: deps.steps
                # only reach the run as the condensed decision log, and a hard
                # stop must stay visible even on a run that had already built a
                # report before the model kept calling a spent tool.
                trace.entries.append(
                    {"kind": "budget", "status": "error", "error": deps.abort_reason}
                )
                break


def _assemble(deps: _SdkDeps, trace: SdkTrace, question: str, plan: str) -> dict[str, Any] | None:
    """Turn a finished run into the answer dict ``main._answer`` expects."""
    if deps.report is None:
        if deps.no_answer:
            return _no_answer_result(deps, trace)
        return _salvage(deps, trace, deps.abort_reason or "model never produced a report")

    report = {
        **deps.report,
        "queries": _query_list(deps.queries),
        "knowledge_pages_used": _knowledge_used(deps, trace),
        "knowledge_version": knowledge_version(),
    }
    pages, page_steps = compose_pages(report, question=question)
    allowed_kinds = set(planned_kinds(plan))
    pages = [p for p in pages if p.get("kind", p["template"]) in allowed_kinds]
    if pages:
        report["pages"] = pages
    deps.steps.extend(page_steps)
    for p in pages:
        kind = p.get("kind", p["template"])
        if kind not in deps.pages_emitted:
            deps.emit_page(kind, p)
    deps.emit_skipped_pages()

    steps = _merge_decision_log(list(trace.entries), deps.steps)
    steps.extend(page_steps)
    steps.append(
        {
            "kind": "analysis",
            "skills_used": deps.skills_used,
            "skill_gaps": deps.skill_gaps,
            "used_inline_math": deps.used_inline_math,
        }
    )
    usage = trace.usage_totals(settings.sdk_model)

    primary = select_primary_query(deps.queries)
    return {
        "answer": report.get("summary", ""),
        "report": report,
        "pages": pages or None,
        "sql": primary.get("sql") if primary else None,
        "columns": primary.get("columns", []) if primary else [],
        "rows": primary.get("rows", []) if primary else [],
        "row_count": primary.get("row_count", 0) if primary else 0,
        "chart": report.get("main_chart"),
        "engine": ENGINE,
        "steps": steps,
        "attempts": deps.attempts,
        **usage,
    }


def _knowledge_used(deps: _SdkDeps, trace: SdkTrace) -> list[str]:
    """Pages the run consulted: the hook's counted reads plus anything the
    trace saw (a page opened by a tool call the hook did not classify)."""
    used = list(deps.knowledge_pages)
    for name in trace.knowledge_pages:
        if name not in used:
            used.append(name)
    return used


def _salvage(deps: _SdkDeps, trace: SdkTrace, why: str) -> dict[str, Any]:
    """Package a failed run's trace so the stub fallback keeps its spend visible."""
    deps.emit_skipped_pages()
    steps = _merge_decision_log(list(trace.entries), deps.steps)
    steps.append({"kind": "fallback", "status": "error", "error": why, "to": "stub"})
    return {
        "fallback": True,
        "degraded": True,
        "steps": steps,
        "attempts": deps.attempts,
        **trace.usage_totals(settings.sdk_model),
    }


def _no_answer_result(deps: _SdkDeps, trace: SdkTrace) -> dict[str, Any]:
    """An honest 'this data can't answer that', in the report-compatible envelope."""
    deps.emit_skipped_pages()
    steps = _merge_decision_log(list(trace.entries), deps.steps)
    report = {
        "element_id": "report",
        "summary": deps.no_answer or "The available data can't answer that question.",
        "headlines": [],
        "insights": [],
        "profiles": [],
        "main_chart": None,
        "queries": _query_list(deps.queries),
        "knowledge_pages_used": _knowledge_used(deps, trace),
        "knowledge_version": knowledge_version(),
        "no_answer": True,
    }
    return {
        "answer": report["summary"],
        "report": report,
        "sql": None,
        "columns": [],
        "rows": [],
        "row_count": 0,
        "chart": None,
        "engine": ENGINE,
        "steps": steps,
        "attempts": deps.attempts,
        **trace.usage_totals(settings.sdk_model),
    }
