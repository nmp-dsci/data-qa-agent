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
from .deck import DEFAULT_CATALOGUE, DeckBuilder, Layout, load_catalogue, render_layouts_md
from .gsuite import GoogleClient, credentials_present
from .knowledge import knowledge_version
from .memory import recall_memories
from .ordinals import ordinals_snapshot_hash
from .otlp import agent_span
from .pack import PackSpec, load_pack, pack_path, pack_to_catalogue
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
from .version import build_sdk_fingerprint
from .workspace import workspace

# Recorded as AgentAnswer.engine, the way the stub records "stub" and the demo
# replayer records "demo_replay" — so a run's runtime is visible in
# app.query_runs without inferring it from the model name.
ENGINE = "agent_sdk"

MCP_SERVER = "dp"
BUILTIN_TOOLS = ["Read", "Grep", "Glob"]
GOVERNED_TOOLS = ["extract", "run_analysis", "lookup_values", "no_answer", "remember"]
# s46: registered only when a generating credential is configured AND export is
# on, so a run that cannot build a deck is never offered the tools.
DECK_TOOLS = ["start_deck", "add_slide"]


def deck_enabled() -> bool:
    return bool(settings.deck_export) and credentials_present()


def governed_tools() -> list[str]:
    return [*GOVERNED_TOOLS, *(DECK_TOOLS if deck_enabled() else [])]


def allowed_tools() -> list[str]:
    return [*BUILTIN_TOOLS, *(f"mcp__{MCP_SERVER}__{t}" for t in governed_tools())]


# Retained for callers/tests that want the champion-era surface.
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
    # s46: the run's deck, created lazily by start_deck. None until the agent
    # reaches the visualisation phase (or forever, if export is off).
    deck: Any = None
    slide_calls: int = 0
    # s48: the run's identity, so the deck can stamp its footer and its Drive
    # appProperties without the tools having to be re-plumbed a run context.
    run_id: str = ""
    question: str = ""
    # frame name -> the extract that produced it, so a slide can name its query
    # in the manifest (the Sources & SQL slide is built from these).
    frame_refs: dict[str, str] = field(default_factory=dict)
    # Set when a tool blows its budget past the courtesy STOP. The message loop
    # checks it after every message and closes the query — the SDK has no
    # exception channel back into the model's loop, so this is how the
    # champion's SandboxBudgetExhausted hard stop is reproduced.
    abort_reason: str | None = None
    knowledge_denials: int = 0
    # s48 §7: the deck's version-1 snapshot, taken by the builder at finish().
    deck_baseline: dict[str, Any] = field(default_factory=dict)
    hook_events: list[dict[str, Any]] = field(default_factory=list)

    def after_frame(self, name: str, frame: Any) -> None:
        """Mirror a head sample of the extracted frame into ``frames/``.

        Best-effort: the frame is already in ``self.frames`` for run_analysis, so
        a filesystem hiccup here must never fail the extract. Purely an
        affordance for the model to eyeball what it pulled.
        """
        if self.ws is None:
            return
        if self.queries:
            self.frame_refs[name] = list(self.queries)[-1]
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
    "start_deck": (
        "Open the answer's Google Slides deck and its backing Sheet. Call ONCE, "
        "after your analysis is done and before the first add_slide.\n\n"
        "Give a short deck title naming the question's subject."
    ),
    "add_slide": (
        "Append one slide to the deck, filling it in a single atomic write.\n\n"
        "`layout` must be an exact name from layouts.md (Grep it first). Pass "
        "`frame` to chart or tabulate a frame you already extracted \u2014 the rows "
        "are written to the Sheet and the chart is NATIVE and editable, not a "
        "picture. `headline` is the slide title; `commentary` is one or two "
        "sentences on what the numbers mean, not what they show. Use `columns` "
        "to pick and order which of the frame's columns to plot; the first is "
        "the x axis / label column, which must be unique per row for a chart — "
        "aggregate first, or pass a categorical second column and it is "
        "pivoted into series automatically. On a KPI layout, `kpi` is the "
        "number itself and the optional `kpi_label` says what it measures "
        "(e.g. 'median sale price'). The footer and the source line are "
        "filled in for you."
    ),
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
    "start_deck": _schema({"title": "string"}, required=["title"]),
    "add_slide": {
        "type": "object",
        "properties": {
            "layout": {"type": "string"},
            "headline": {"type": "string"},
            "commentary": {"type": "string"},
            "kpi": {"type": "string"},
            "kpi_label": {"type": "string"},
            "frame": {"type": "string"},
            "columns": {"type": "array", "items": {"type": "string"}},
            "chart_type": {
                "type": "string",
                "enum": ["line", "bar", "column", "area", "scatter"],
            },
        },
        "required": ["layout", "headline"],
    },
}


# The first schema-qualified relation a query reads — what the deck's `source`
# line and the Sources & SQL slide name. A regex, not sqlglot: this is a label
# on a slide, and the guardrail that actually decides what may be read has
# already run by the time a slide is built.
_MART_RE = re.compile(r"\b(?:from|join)\s+([a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*)", re.IGNORECASE)


def mart_of(sql: str) -> str:
    match = _MART_RE.search(sql or "")
    return match.group(1) if match else ""


@dataclass
class DeckContext:
    """Everything the deck tools need that is not per-call state."""

    client: GoogleClient
    catalogue: tuple[Layout, ...]
    template_id: str
    # s48: present when packs/<PACK_NAME>/pack.json was loaded; None = the s46
    # built-in catalogue, which builds slides from predefined layouts instead.
    pack: PackSpec | None = None


# Cap rows written per slide. The marts are pre-aggregated, so a legitimate
# monthly series is a few hundred rows; past this a chart is unreadable anyway
# and the Sheet write gets slow.
MAX_SLIDE_ROWS = 500

# Rows a table slide can show before it stops being readable. Not a cap — the
# slide is still built — but the model gets told, because the alternative is
# what was observed: it added two 495-row slides, judged them wrong on its own,
# and spent the rest of its turn budget re-extracting and redoing them.
READABLE_TABLE_ROWS = 15

# What ``_do_run_analysis`` says when the report is finished. It predates the
# deck and tells the model the run is over — which, with deck tools registered,
# lands exactly where step 6 (PRESENT) should begin and stops the agent one turn
# short of the deliverable. Observed doing precisely that: the model answered
# "Now let's build the deck", grepped layouts.md, and ended.
#
# Rewritten here rather than in sandbox_agent.py because that string is the
# pydantic-ai champion's contract, and the champion has no deck to build.
_CONFIRM_SUFFIX = "Now return a one-line confirmation."
_DECK_SUFFIX = (
    "Now do step 6 (PRESENT): read layouts.md, call start_deck once, then "
    "add_slide per slide. The deck IS the deliverable — do not stop here. "
    "Return the one-line confirmation only after the last slide is added."
)


def _with_deck_next_step(out: str, deck_active: bool) -> str:
    """Point the model at the deck instead of at the exit, when there is one."""
    if not deck_active or _CONFIRM_SUFFIX not in out:
        return out
    return out.replace(_CONFIRM_SUFFIX, _DECK_SUFFIX)


def _cell(value: Any) -> Any:
    """JSON-safe scalar for a Sheets cell (numpy scalars, NaT, Timestamps)."""
    if value is None:
        return ""
    item = getattr(value, "item", None)
    if callable(item):
        try:
            value = item()
        except (ValueError, TypeError):
            return str(value)
    if isinstance(value, (int, float, str, bool)):
        # NaN is not valid JSON and Sheets rejects it.
        if isinstance(value, float) and value != value:  # noqa: PLR0124 — NaN check
            return ""
        return value
    return str(value)


def _frame_table(frame: Any, columns: list[str] | None) -> tuple[list[str], list[list[Any]]]:
    """(columns, rows) for a pandas frame, honouring an explicit column choice."""
    sub = frame
    if columns:
        wanted = [c for c in columns if c in frame.columns]
        if wanted:
            sub = frame[wanted]
    sub = sub.head(MAX_SLIDE_ROWS)
    cols = [str(c) for c in sub.columns]
    rows = [[_cell(v) for v in rec] for rec in sub.itertuples(index=False)]
    return cols, rows


def build_tool_server(
    sdk: Any,
    deps: _SdkDeps,
    *,
    max_extracts: int,
    max_runs: int,
    deck_ctx: DeckContext | None = None,
) -> Any:
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
        return _text(
            _with_deck_next_step(out, deck_ctx is not None), is_error=out.startswith("STOP:")
        )

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

    async def start_deck_tool(args: dict[str, Any]) -> dict[str, Any]:
        if deck_ctx is None:
            return _text("STOP: deck export is not configured for this run.", is_error=True)
        if deps.deck is not None:
            return _text("The deck is already open — go straight to add_slide.")
        title = str(args.get("title") or "").strip() or "Analysis"
        pack = deck_ctx.pack
        builder = DeckBuilder(
            client=deck_ctx.client,
            catalogue=deck_ctx.catalogue,
            title=title,
            run_id=deps.run_id,
            question=deps.question,
            pack_name=pack.name if pack else "",
            pack_version=pack.version if pack else 0,
            pack_sheet_id=pack.sheet_id if pack else "",
            table_templates=dict(pack.table_templates) if pack else {},
        )
        try:
            await builder.start(deck_ctx.template_id)
        except Exception as exc:  # noqa: BLE001 — surfaced to the model to react to
            return _text(f"Could not open the deck: {exc}", is_error=True)
        deps.deck = builder
        names = ", ".join(builder.layout_names())
        return _text(
            f"Deck opened. Layouts available: {names}. "
            f"Add up to {settings.max_slides} slides with add_slide."
        )

    async def add_slide_tool(args: dict[str, Any]) -> dict[str, Any]:
        if deck_ctx is None:
            return _text("STOP: deck export is not configured for this run.", is_error=True)
        builder = deps.deck
        if builder is None:
            return _text("Call start_deck first.", is_error=True)
        if deps.slide_calls >= settings.max_slides:
            # Mirrors the extract/run_analysis budget: the global turn ceiling
            # can be loosened, but a per-tool counter still bounds the blast
            # radius of a model that keeps adding slides.
            deps.abort_reason = f"slide budget exhausted ({settings.max_slides})"
            return _text(f"STOP: {deps.abort_reason}", is_error=True)

        layout = builder.layout(str(args.get("layout") or ""))
        if layout is None:
            # A bad guess costs one turn and gets the real menu back, rather
            # than producing a broken slide.
            return _text(
                f"Unknown layout {args.get('layout')!r}. Choose one of: "
                f"{', '.join(builder.layout_names())}",
                is_error=True,
            )

        columns: list[str] | None = None
        rows: list[list[Any]] | None = None
        frame_name = str(args.get("frame") or "").strip()
        if frame_name:
            frame = deps.frames.get(frame_name)
            if frame is None:
                available = ", ".join(sorted(deps.frames)) or "none yet"
                return _text(
                    f"No extracted frame named {frame_name!r}. Available: {available}",
                    is_error=True,
                )
            wanted = [str(c) for c in (args.get("columns") or [])]
            columns, rows = _frame_table(frame, wanted)
            if not rows:
                return _text(f"Frame {frame_name!r} is empty.", is_error=True)
            # A chart needs a label column AND at least one measure. Selecting a
            # single column silently produced a Sheets chart plotting labels
            # against nothing — valid to the API, meaningless on the slide. Fail
            # here with the frame's real columns rather than building it.
            if layout.wants_chart and len(columns) < 2:
                available = ", ".join(str(c) for c in frame.columns)
                return _text(
                    f"A chart needs at least two columns — the first is the x axis/label, "
                    f"the rest are plotted. Got {columns!r}. Columns in {frame_name!r}: "
                    f"{available}",
                    is_error=True,
                )

        deps.slide_calls += 1
        query_ref = deps.frame_refs.get(frame_name, "")
        try:
            record = await builder.add_slide(
                layout=layout,
                headline=str(args.get("headline") or "").strip(),
                commentary=str(args.get("commentary") or "").strip(),
                kpi=str(args.get("kpi") or "").strip(),
                kpi_label=str(args.get("kpi_label") or "").strip(),
                columns=columns,
                rows=rows,
                chart_type=str(args.get("chart_type") or "") or None,
                tab_name=frame_name,
                query_ref=query_ref,
                mart=mart_of(deps.queries.get(query_ref, {}).get("sql", "")),
            )
        except Exception as exc:  # noqa: BLE001 — the model can retry a slide
            return _text(f"Slide not added: {exc}", is_error=True)
        what = "chart" if record.has_chart else ("table" if record.has_table else "text")
        message = (
            f"Slide {record.index + 1} added ({layout.name}, {what}"
            f"{f', {record.rows} rows' if record.rows else ''})."
        )
        if record.dropped:
            # Not an error — the slide is real and correct. But the layout had no
            # region for some of what was passed, and saying so is the difference
            # between the model fixing it and the words vanishing unnoticed.
            fits = ", ".join(
                entry.name
                for entry in builder.catalogue
                if entry.enabled and entry.commentary is not None
            )
            message += (
                f" Note: {' and '.join(record.dropped)} was NOT placed — "
                f"{layout.name} has no region for it. Layouts that take commentary: "
                f"{fits or 'none'}. Re-add this slide with one of those if the text matters."
            )
        if record.has_table and record.rows > READABLE_TABLE_ROWS:
            message += (
                f" Note: {record.rows} rows is far more than a slide can show —"
                f" about {READABLE_TABLE_ROWS} is readable. Pass a `columns` subset"
                " of an already-ranked frame rather than re-extracting."
            )
        return _text(message)

    handlers: dict[str, Any] = {
        "extract": extract_tool,
        "run_analysis": run_analysis_tool,
        "lookup_values": lookup_values_tool,
        "no_answer": no_answer_tool,
        "remember": remember_tool,
    }
    names = list(GOVERNED_TOOLS)
    if deck_ctx is not None:
        handlers["start_deck"] = start_deck_tool
        handlers["add_slide"] = add_slide_tool
        names += DECK_TOOLS
    tools = [
        sdk.tool(name, TOOL_DESCRIPTIONS[name], TOOL_SCHEMAS[name])(handlers[name])
        for name in names
    ]
    return sdk.create_sdk_mcp_server(name=MCP_SERVER, tools=tools)


# ---------------------------------------------------------------------------
# Knowledge-read quota (the champion's max_knowledge_reads, enforced on Read/Grep)
# ---------------------------------------------------------------------------

# Argument keys across Read/Grep/Glob that can carry a filesystem location.
_PATH_ARG_KEYS = ("file_path", "path", "pattern", "notebook_path")


def _knowledge_target(tool_input: Any) -> str | None:
    if not isinstance(tool_input, dict):
        return None
    for key in _PATH_ARG_KEYS:
        page = knowledge_page_from_path(str(tool_input.get(key) or ""))
        if page:
            return page
    return None


def _resolved_path_args(ws: Path, tool_input: Any) -> list[Path]:
    """Every path-shaped argument, resolved against the workspace root.

    Covers Read's ``file_path``, Grep/Glob's ``path`` (base directory) and
    ``pattern`` (Glob's pattern doubles as a path — wildcards resolve fine
    since ``Path.resolve()`` only normalizes ``..``/symlinks, not glob syntax)
    and ``notebook_path``. A relative value is resolved against ``ws`` the
    same way the CLI itself resolves a relative tool argument against ``cwd``.
    """
    if not isinstance(tool_input, dict):
        return []
    resolved = []
    for key in _PATH_ARG_KEYS:
        raw = tool_input.get(key)
        if not raw:
            continue
        candidate = Path(str(raw))
        if not candidate.is_absolute():
            candidate = ws / candidate
        with contextlib.suppress(OSError, ValueError):
            resolved.append(candidate.resolve())
    return resolved


def _within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _search_root(ws: Path, tool_name: str, tool_input: Any) -> Path | None:
    """The effective directory a Grep/Glob call recurses from.

    Both tools default their search root to ``cwd`` (``ws``) when the model
    omits ``path`` — the SDK never sends that default back in ``tool_input``,
    so leaving an absent ``path`` unclassified would let a no-``path`` Grep
    walk the whole workspace, including ``knowledge/``, uncounted. Returning
    ``ws`` itself here makes that default explicit for the caller.
    """
    if tool_name not in ("Grep", "Glob"):
        return None
    raw = tool_input.get("path") if isinstance(tool_input, dict) else None
    if not raw:
        return ws
    candidate = Path(str(raw))
    if not candidate.is_absolute():
        candidate = ws / candidate
    with contextlib.suppress(OSError, ValueError):
        return candidate.resolve()
    return None


def _deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def make_knowledge_hook(deps: _SdkDeps) -> Any:
    """PreToolUse hook scoping Read/Grep/Glob to the workspace and enforcing
    ``max_knowledge_reads`` on Read/Grep of knowledge/.

    Every built-in file tool call is resolved against the per-run workspace
    root first: a path (absolute or, after joining onto ``cwd``, relative)
    that lands outside the workspace is denied outright — the workspace is
    the ONLY filesystem surface this runtime advertises to the model, so
    nothing here may read another run's ``runs_dir`` entry or a host path
    like ``/app/.env``.

    The champion enforces the knowledge cap inside its ``read_knowledge``
    tool. Here the model opens pages with the built-in file tools, so the cap
    has to live where the CLI asks permission — this same hook. Re-reading a
    page already loaded is free (it is already in context), matching the
    champion's "(already loaded …)" short-circuit; a genuinely new page past
    the cap is denied with the champion's wording. A directory-scoped
    Grep/Glob over ``knowledge/`` that names no single page would otherwise
    read the whole tree in one uncounted call, so it is denied too — the
    model must open pages one at a time to stay inside the quota.
    """

    async def hook(input_data: Any, tool_use_id: str | None, context: Any) -> dict[str, Any]:
        data = input_data if isinstance(input_data, dict) else {}
        if data.get("tool_name") not in BUILTIN_TOOLS:
            return {}
        tool_input = data.get("tool_input")
        resolved_args: list[Path] = []
        if deps.ws is not None:
            ws = deps.ws.resolve()
            resolved_args = _resolved_path_args(ws, tool_input)
            if any(not _within(ws, resolved) for resolved in resolved_args):
                return _deny("path is outside the run workspace")
        page = _knowledge_target(tool_input)
        if page is None:
            if deps.ws is not None:
                ws = deps.ws.resolve()
                knowledge_dir = ws / "knowledge"
                touches_knowledge = any(
                    _within(knowledge_dir, resolved) for resolved in resolved_args
                )
                if not touches_knowledge:
                    search_root = _search_root(ws, str(data.get("tool_name") or ""), tool_input)
                    touches_knowledge = search_root is not None and (
                        search_root == knowledge_dir or _within(search_root, knowledge_dir)
                    )
                if touches_knowledge:
                    deps.knowledge_denials += 1
                    return _deny(
                        "read knowledge pages one at a time by path; "
                        "whole-directory reads are not permitted"
                    )
            return {}
        if page in deps.knowledge_pages:
            return {}
        if deps.knowledge_reads >= settings.max_knowledge_reads:
            deps.knowledge_denials += 1
            return _deny("knowledge read limit reached; proceed with the pages you have.")
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


def _finish_span(span: Any, *, deps: _SdkDeps, trace: SdkTrace | None) -> None:
    """Attach the run's outcome to its OTel span (s44 M3b).

    Split from the span's creation attributes because these are only known
    once the run — or its failure — has actually happened: turns, tokens, cost
    and pages-emitted all come from state ``_drive``/``_assemble`` built up
    over the run. A no-op-safe ``span`` (see ``otlp._NoOpSpan``) makes every
    call here free when ``OTLP_ENDPOINT`` is unset.

    ``ok`` is computed from ``deps`` rather than passed in by the caller: a run
    that hits a hard stop (a spent budget) returns cleanly through the SAME
    code path as a real answer — no exception is raised, ``_assemble`` just
    hands back a salvage dict — so "did an exception propagate" is not the
    same question as "did this run produce something usable". A real report or
    an honest ``no_answer`` is ``ok``; the salvage/fallback envelope (whether
    reached via a clean hard stop or via a caught exception) is not.
    ``aborted`` is the separate, narrower signal for specifically the budget
    hard-stop case.
    """
    ok = deps.report is not None or bool(deps.no_answer)
    usage = trace.usage_totals(settings.sdk_model) if trace is not None else {}
    span.set_attribute("ok", ok)
    span.set_attribute("aborted", bool(deps.abort_reason))
    span.set_attribute("num_turns", trace.num_turns if trace is not None else 0)
    span.set_attribute("pages_emitted", len(deps.pages_emitted))
    if trace is not None and trace.session_id:
        span.set_attribute("session_id", trace.session_id)
    usage_keys = (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "cost_usd",
    )
    for key in usage_keys:
        value = usage.get(key)
        if value is not None:
            span.set_attribute(key, value)


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

    The whole run is wrapped in one OTel span (s44 M3b) — this runtime has no
    library auto-instrumenting the model turns the way logfire's pydantic-ai
    instrumentation does for the champion, so this is the only span a slow or
    failing SDK run gets. Nested inside the FastAPI-instrumented ``/agent/ask``
    request span (main.py), it inherits that request's trace id — the same one
    backend-api reads off its own ambient span and stamps onto
    ``app.query_runs.otel_trace_id`` (see ``backend-api/app/tracing.py``'s
    ``current_trace_id()``), so the two services' traces are one trace, not two
    to correlate by hand.
    """
    sdk = _load_sdk()
    rid = run_id or uuid.uuid4().hex
    deps = _SdkDeps(user_id=user_id, progress=progress, user_plan=plan, question=question)
    trace: SdkTrace | None = None
    # The sync fingerprint (no DB read) so the span's identity always matches
    # exactly what /agent/version returns and what backend-api resolves to an
    # app.agent_versions row — see version.build_sdk_fingerprint's docstring.
    fingerprint = build_sdk_fingerprint()
    with agent_span(
        "agent_sdk.answer",
        question_length=len(question),
        run_id=rid,
        agent_version_fingerprint=fingerprint.get("fingerprint"),
        model=settings.sdk_model,
    ) as span:
        # The TRUE live ordinals state (DB overrides included) for this run,
        # as an extra diagnostic attribute — never folded into the fingerprint
        # identity above, which stays DB-independent. Best-effort: this must
        # never be why a run fails, even though ordinals_snapshot_hash() itself
        # already never raises.
        with contextlib.suppress(Exception):
            span.set_attribute("ordinals_snapshot_hash", await ordinals_snapshot_hash())
        try:
            # The page plan is deterministic policy per user (s10): declared and
            # emitted BEFORE any model work, so the frontend draws its ghost slots
            # while the CLI is still spawning.
            plan_slots = page_plan(plan=plan)
            deps.run_id = rid
            deps.page_indexes = {
                s["kind"]: s["index"] for s in plan_slots if s["status"] != "locked"
            }
            deps.emit_frame("plan", {"pages": plan_slots})

            recalled = await recall_memories(user_id, question)
            include_insights = "insights" in deps.page_indexes
            base_dir = Path(settings.sdk_workspace_dir) if settings.sdk_workspace_dir else None
            deck_ctx = await _build_deck_context()
            layouts_md = render_layouts_md(deck_ctx.catalogue) if deck_ctx else ""
            async with workspace(
                rid,
                question,
                include_insights=include_insights,
                memories_block=_memories_block(recalled),
                layouts_md=layouts_md,
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
                    deck_ctx=deck_ctx,
                )
                tool_names = allowed_tools()
                options = sdk.ClaudeAgentOptions(
                    model=settings.sdk_model,
                    cwd=str(ws),
                    system_prompt=system_prompt,
                    # The deck is a whole extra phase after the report is done —
                    # read layouts.md, start_deck, then one add_slide per slide.
                    # On the default ceiling both proof questions ended at an
                    # identical turn count with the report built and the deck
                    # never started: they ran out mid-PRESENT. The allowance is
                    # additive and only applies when the tools are registered, so
                    # a run without deck export keeps the old ceiling exactly.
                    max_turns=settings.agent_request_limit
                    + (2 * settings.max_slides + 4 if deck_ctx is not None else 0),
                    mcp_servers={MCP_SERVER: server},
                    # `tools` restricts the built-in toolset (no Bash/Write/Edit in
                    # the workspace); `allowed_tools` auto-approves what is left, so
                    # a headless run never blocks on a permission prompt.
                    tools=list(tool_names),
                    allowed_tools=list(tool_names),
                    hooks={"PreToolUse": [sdk.HookMatcher(hooks=[make_knowledge_hook(deps)])]},
                    env=cli_env(),
                )
                await _drive(sdk, options, question, deps, trace)
                await _finish_deck(deps)
                await _publish_deck(deps)

            result = _assemble(deps, trace, question, plan)
            _finish_span(span, deps=deps, trace=trace)
            return result
        except AgentSdkUnavailable:
            _finish_span(span, deps=deps, trace=trace)
            raise  # a misconfiguration, not a runtime failure — never hide it
        except Exception as exc:  # noqa: BLE001 — never let this path break the app
            if trace is None:
                print(f"[data-agent] agent_sdk path unavailable, using stub: {exc}")
                _finish_span(span, deps=deps, trace=trace)
                return None
            if deps.report is not None or deps.no_answer:
                # The champion salvages the same way: a failure AFTER the report was
                # built (a dropped transport on the confirmation turn, say) must
                # still deliver the report the user already watched stream in.
                print(f"[data-agent] agent_sdk run errored ({exc}); using result built so far")
                with contextlib.suppress(Exception):
                    result = _assemble(deps, trace, question, plan)
                    _finish_span(span, deps=deps, trace=trace)
                    return result
            print(f"[data-agent] agent_sdk path unavailable, using stub: {exc}")
            out = _salvage(deps, trace, str(exc))
            _finish_span(span, deps=deps, trace=trace)
            return out


# ---------------------------------------------------------------------------
# Deck export (s46)
# ---------------------------------------------------------------------------

# One client and one catalogue per process. The catalogue is a property of the
# pack, not of a run, and reading it costs a Slides round trip — so a deck build
# stays one batch per slide rather than paying a discovery call every question.
_deck_client: GoogleClient | None = None
_catalogue_cache: dict[str, tuple[Layout, ...]] = {}
_pack_catalogue_cache: dict[str, tuple[Layout, ...]] = {}


async def _build_deck_context() -> DeckContext | None:
    """The run's deck context, or None when export is off or unconfigured.

    Returning None is what withholds the tools: ``build_tool_server`` registers
    ``start_deck``/``add_slide`` only when this is non-None, so a run that cannot
    build a deck never sees them in its tool list.
    """
    global _deck_client
    if not deck_enabled():
        return None
    if _deck_client is None:
        _deck_client = GoogleClient()
    template_id = settings.google_slides_template_id
    # s48: a synced template pack wins. It is a repo file, so this costs no
    # round trip — and it is the ONLY way a curator's `use_when` sentences and
    # slot geometry reach the agent.
    pack = load_pack(pack_path(settings.pack_dir, settings.pack_name))
    if pack is not None and pack.slides_id:
        catalogue = _pack_catalogue_cache.get(pack.slides_id)
        if catalogue is None:
            catalogue = pack_to_catalogue(pack)
            _pack_catalogue_cache[pack.slides_id] = catalogue
        return DeckContext(
            client=_deck_client,
            catalogue=catalogue,
            template_id=pack.slides_id,
            pack=pack,
        )
    catalogue = _catalogue_cache.get(template_id)
    if catalogue is None:
        try:
            catalogue = await load_catalogue(_deck_client, template_id)
        except Exception as exc:  # noqa: BLE001 — a bad pack must not fail the answer
            print(f"[data-agent] slide pack unreadable ({exc}); using the built-in catalogue")
            catalogue = DEFAULT_CATALOGUE
        _catalogue_cache[template_id] = catalogue
    return DeckContext(client=_deck_client, catalogue=catalogue, template_id=template_id)


def _deck_sources(deps: _SdkDeps) -> list[dict[str, Any]]:
    """Every extract this run made, for the auto-appended Sources & SQL slide."""
    return [
        {
            "ref": ref,
            "mart": mart_of(str(q.get("sql") or "")),
            "rows": int(q.get("row_count") or 0),
            "sql": str(q.get("sql") or ""),
        }
        for ref, q in deps.queries.items()
    ]


async def _finish_deck(deps: _SdkDeps) -> None:
    """Close the deck: Sources & SQL, clear the library slides, write the Sheet.

    The version-1 baseline snapshot comes back from here and rides on the
    artifact, so the change-log workstream stores what the builder saw rather
    than re-reading a deck that may already have been edited.
    """
    builder = deps.deck
    if builder is None:
        return
    try:
        deps.deck_baseline = await builder.finish(_deck_sources(deps))
    except Exception as exc:  # noqa: BLE001 — an unfinished deck is still a deck
        print(f"[data-agent] deck finish failed ({exc}); the slides are still there")


async def _publish_deck(deps: _SdkDeps) -> None:
    """Share the run's artifacts, if this deployment is allowed to.

    ``deck_public`` is a separate flag from ``deck_export`` on purpose. Sharing
    anyone-with-link puts a file outside RLS permanently and a leaked link cannot
    be un-published, so the capability is gated here rather than inferred from
    having credentials.
    """
    builder = deps.deck
    if builder is None or not settings.deck_public:
        return
    try:
        await builder.publish()
    except Exception as exc:  # noqa: BLE001 — an unshared deck is still a deck
        print(f"[data-agent] deck sharing failed ({exc}); artifacts stay private")


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
    budget. The cumulative-token check mirrors the champion's
    ``UsageLimits(total_tokens_limit=...)`` — ``max_turns`` alone caps request
    count, not spend, so a run that loops on large Read/Grep output is stopped
    by tokens too.
    """
    stream = sdk.query(prompt=question, options=options)
    async with contextlib.aclosing(stream) as messages:
        async for msg in messages:
            trace.consume(msg)
            if (
                deps.abort_reason is None
                and trace.total_tokens >= settings.agent_total_tokens_limit
            ):
                deps.abort_reason = (
                    f"token budget exhausted ({trace.total_tokens}/"
                    f"{settings.agent_total_tokens_limit})"
                )
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
        # s46: the Sheets/Slides artifact this run produced, or None when deck
        # export is off. Carried alongside the report rather than inside it, so
        # backend-api can persist the URLs without reshaping the report.
        "artifact": _artifact(deps),
        **usage,
    }


def _artifact(deps: _SdkDeps) -> dict[str, Any] | None:
    """The run's Sheets/Slides artifact, or None when deck export is off.

    ``baseline`` is the §7 version-1 snapshot: carried on the artifact rather
    than written from here, so this module never grows a database dependency and
    the change-log workstream owns the storage.
    """
    if deps.deck is None:
        return None
    manifest = dict(deps.deck.manifest())
    if deps.deck_baseline:
        manifest["baseline"] = deps.deck_baseline
    return manifest


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
