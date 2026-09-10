"""Per-run filesystem workspace for the Claude Agent SDK runtime (M2).

The pydantic-ai ReAct loop pins schema/knowledge straight into the system
prompt and exposes ``search_knowledge``/``read_knowledge``/``describe_table``
tools (see ``sandbox_agent.py:_sandbox_system_prompt``). The Agent SDK runtime
instead gives the model a real per-run directory it explores with
Read/Grep/Glob:

    <base>/runs/<run_id>/
        CLAUDE.md       — rendered workflow instructions (tier 0 prompt)
        marts.md        — tier 0: the mart index (agent.schema.list_marts())
        schema/*.md     — tier 1: one file per queryable table (describe_table)
        knowledge/      — tier 2: a copy of the Insight Playbook markdown tree
        layouts.md      — s46: the curated slide-layout catalogue, when deck
                          export is on. The agent Greps it before naming a
                          layout, the same motion it uses for knowledge pages.
        frames/         — extract() drops head-sample CSVs here at runtime

This module only *builds* that directory — no Claude Agent SDK import here by
design, so it stays testable offline and independent of which runtime wires
it up. ``schema.list_marts()``/``schema.describe_table()`` are pure-Python
(dbt-manifest or curated-catalog fallback, no DB) so building a workspace never
touches the database or the network.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from . import knowledge as knowledge_mod
from .config import settings
from .schema import USER_VISIBLE_SCHEMAS, describe_table, get_catalog, list_marts

_TEMPLATE_PATH = Path(__file__).resolve().parent / "prompts" / "workspace_claude.md"
_FRAMES_README = "extract() drops a head-sample CSV of each frame it pulls here at runtime.\n"

# run_id becomes a directory name under <base>/runs/ — keep it to a safe,
# unambiguous character set so it can never escape that directory (no path
# separators, no "..", no leading dot).
_SAFE_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _default_base_dir() -> Path:
    return Path(tempfile.gettempdir()) / "data-agent-workspaces"


def _sanitize_run_id(run_id: str) -> str:
    if not run_id or not _SAFE_RUN_ID_RE.match(run_id) or ".." in run_id:
        raise ValueError(f"unsafe run_id: {run_id!r}")
    return run_id


def _pass_plan(*, include_insights: bool, max_runs: int) -> str:
    """Port of ``sandbox_agent._sandbox_system_prompt``'s pass-plan branch.

    Keeps the wording the champion (pydantic-ai) prompt uses so an eval-gate
    comparison between the two runtimes isn't confounded by a rewritten prompt.
    """
    if include_insights:
        return f"""\
   The app STREAMS each page to the user the moment you finish it, so work in
   TWO SHORT PASSES over the frame(s) you already extracted (never re-extract):
   PASS 1 — the summary page (always FIRST, keep it FAST — no attribute
   slicing here): aggregate to headline level and assign
       result = skills.build_report(
           summary="...",
           headlines=[{{"label": "...", "value": ..., "basis": ...}}],
           main_chart=skills.trend_chart(s, title="..."),
       )
       Always include a latest_value + growth headline and the trend/comparison
       chart — this page captures the answer and renders IMMEDIATELY.
   PASS 2 — the insights page (a SECOND run_analysis call, right after pass 1
   succeeds): slice the SAME frame by its attribute columns (e.g. a type or
   band; call driver_analysis when the question asks WHY or attributes exist)
   and assign
       result = skills.build_insights(insights=[
           skills.make_insight("...", "...", chart=skills.comparison_chart(...)),
       ])
       naming the strongest driver of the Page-1 numbers with a
       comparison_chart of its levels. It merges into the pass-1 report.
   You get up to {max_runs} run_analysis attempts TOTAL across both passes; if
   a pass returns an error, fix the code and retry."""
    return f"""\
   Write SHORT pandas that aggregates to headline level and assigns the report:
       result = skills.build_report(
           summary="...",
           headlines=[{{"label": "...", "value": ..., "basis": ...}}],
           main_chart=skills.trend_chart(s, title="..."),
       )
       Always include a latest_value + growth headline and the trend/comparison
       chart — one summary page IS the answer; do not add insights.
   You get up to {max_runs} run_analysis attempts; if it returns an error, fix
   the code and retry."""


def _render_claude_md(*, include_insights: bool, memories_block: str) -> str:
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    pass_plan = _pass_plan(
        include_insights=include_insights, max_runs=settings.sandbox_run_attempts
    )
    memories_section = (
        f"\nKnown preferences for this user:\n{memories_block}\n" if memories_block.strip() else ""
    )
    return template.replace("{{PASS_PLAN}}", pass_plan).replace("{{MEMORIES}}", memories_section)


def _schema_filename(schema: str, table: str) -> str:
    """'schema.table' with the dot flattened to an underscore, e.g. 'marts_property_sales.md'."""
    return f"{schema}_{table}.md"


def build_workspace(
    run_id: str,
    question: str,
    *,
    include_insights: bool,
    memories_block: str = "",
    layouts_md: str = "",
    base_dir: Path | None = None,
) -> Path:
    """Build ``<base>/runs/<run_id>/`` and return its path.

    ``question`` is accepted (not rendered into CLAUDE.md) so the caller's
    signature matches how the run is invoked: the question is sent as the
    query/initial user turn to the Agent SDK runtime, never baked into the
    versioned prompt template — see ``{{QUESTION}}`` NOT being a template slot.
    """
    if not question.strip():
        raise ValueError("question must not be empty")
    safe_run_id = _sanitize_run_id(run_id)
    base = (base_dir or _default_base_dir()).resolve()
    runs_dir = base / "runs"
    ws = runs_dir / safe_run_id
    # Defense in depth: even a run_id that slipped past the regex could not
    # resolve outside runs_dir once joined here, but confirm it explicitly.
    if ws.parent != runs_dir:
        raise ValueError(f"unsafe run_id: {run_id!r}")

    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True)

    (ws / "CLAUDE.md").write_text(
        _render_claude_md(include_insights=include_insights, memories_block=memories_block),
        encoding="utf-8",
    )
    (ws / "marts.md").write_text(list_marts(), encoding="utf-8")
    # Only written when deck export is on, so a run without it keeps the exact
    # workspace — and therefore the exact av-* fingerprint — it had before s46.
    if layouts_md.strip():
        (ws / "layouts.md").write_text(layouts_md, encoding="utf-8")

    schema_dir = ws / "schema"
    schema_dir.mkdir()
    for table in get_catalog(role="user"):
        if table["schema"] not in USER_VISIBLE_SCHEMAS:
            continue
        relation = f"{table['schema']}.{table['table']}"
        filename = _schema_filename(table["schema"], table["table"])
        (schema_dir / filename).write_text(describe_table(relation), encoding="utf-8")

    knowledge_src = knowledge_mod._knowledge_dir()  # noqa: SLF001 — same resolution knowledge_version() uses
    knowledge_dst = ws / "knowledge"
    if knowledge_src.exists():
        shutil.copytree(
            knowledge_src,
            knowledge_dst,
            ignore=shutil.ignore_patterns("__pycache__", ".*"),
        )
    else:
        knowledge_dst.mkdir()

    frames_dir = ws / "frames"
    frames_dir.mkdir()
    (frames_dir / "README.md").write_text(_FRAMES_README, encoding="utf-8")

    return ws


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_tree(root: Path) -> str:
    """Deterministic content hash of a directory's files (order-independent of the fs)."""
    h = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def workspace_manifest(ws: Path) -> dict[str, str]:
    """Per-component sha256 content hashes, feeding the av-* run fingerprint.

    ``knowledge`` reuses ``agent.knowledge.knowledge_version()`` (hashed from
    the live knowledge source tree, not the copy inside ``ws``) so this figure
    agrees with the value already recorded on every report via
    ``report["knowledge_version"]``.
    """
    claude_md = _sha256_bytes((ws / "CLAUDE.md").read_bytes())
    marts = _sha256_bytes((ws / "marts.md").read_bytes())
    schema = _sha256_tree(ws / "schema")
    knowledge = knowledge_mod.knowledge_version()
    # The curated layout catalogue is prompt surface — the agent reads it to
    # choose a layout — so curating it must move the agent version. Absent
    # (deck export off) it contributes nothing, keeping pre-s46 runs identical.
    layouts_path = ws / "layouts.md"
    layouts = _sha256_bytes(layouts_path.read_bytes()) if layouts_path.exists() else ""
    parts = [claude_md, marts, schema, knowledge]
    if layouts:
        parts.append(layouts)
    combined = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return {
        "claude_md": claude_md,
        "marts": marts,
        "schema": schema,
        "knowledge": knowledge,
        "layouts": layouts,
        "combined": combined,
    }


def cleanup_workspace(ws: Path) -> None:
    """Remove a workspace directory built by ``build_workspace``. Safe to call twice."""
    shutil.rmtree(ws, ignore_errors=True)


@asynccontextmanager
async def workspace(
    run_id: str,
    question: str,
    *,
    include_insights: bool,
    memories_block: str = "",
    layouts_md: str = "",
    base_dir: Path | None = None,
) -> AsyncIterator[Path]:
    """Async context manager: build a workspace, yield its path, always clean up.

    The build/cleanup themselves are plain filesystem calls (fast; no
    await-worthy I/O), so this wraps them for API symmetry with the async
    runtime that will drive a run, not because they need the event loop.
    """
    ws = build_workspace(
        run_id,
        question,
        include_insights=include_insights,
        memories_block=memories_block,
        layouts_md=layouts_md,
        base_dir=base_dir,
    )
    try:
        yield ws
    finally:
        cleanup_workspace(ws)
