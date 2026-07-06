"""The hardened sandbox executor — Pyodide/WASM (restructure Phase B).

A drop-in for the Phase A subprocess ``run_code``: same signature, same
``AnalysisResult``, same skills. The difference is the isolation boundary — model
code runs inside Pyodide (CPython in WebAssembly) via a Node host
(``pyodide_host.mjs``), so there are **no syscalls to escape, no host filesystem,
and no network** reachable from the model's Python. pandas/numpy are baked into
the image offline; nothing is fetched at run time.

We spawn one Node process per run (fresh WASM heap + FS each time — isolation as a
side benefit) and enforce a hard wall-clock stop from the parent. Model-code
errors come back as ``result.error`` so the agent self-corrects; an infrastructure
failure (missing Node, timeout, crash) is surfaced the same way rather than
raising, so a broken run degrades to a message instead of taking down the request.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd

from .contract import AnalysisResult, SkillGap
from .runner import _SAFE_BUILTIN_NAMES  # single source of truth for the allowlist

_HOST_SCRIPT = Path(__file__).with_name("pyodide_host.mjs")
_WALLCLOCK_SECONDS = 30  # > subprocess cap: Pyodide cold-starts pandas (~2-4s) per run


def _frame_payload(df: pd.DataFrame) -> dict[str, Any]:
    """Serialise a frame as {columns, rows} with NaN → None (JSON-safe, SQL-shape)."""
    clean = df.astype(object).where(pd.notnull(df), None)
    return {"columns": list(df.columns), "rows": clean.values.tolist()}


def run_code(
    code: str,
    df: pd.DataFrame | None = None,
    *,
    frames: dict[str, pd.DataFrame] | None = None,
) -> AnalysisResult:
    """Execute model-written ``code`` over the injected frame(s) in Pyodide/WASM.

    Mirrors :func:`agent.sandbox.runner.run_code`. Pass one frame as ``df`` or
    several as ``frames``; the report the model assigns to ``result`` comes back
    in :class:`AnalysisResult`, with skills-used / gaps for per-run telemetry.
    """
    all_frames = dict(frames or {})
    if df is not None:
        all_frames.setdefault("df", df)

    node = os.environ.get("NODE_BIN") or shutil.which("node")
    if node is None:
        return AnalysisResult(error="pyodide sandbox unavailable: Node runtime not found")
    if not _HOST_SCRIPT.exists():  # pragma: no cover - packaging guard
        return AnalysisResult(error=f"pyodide host script missing at {_HOST_SCRIPT}")

    job = json.dumps(
        {
            "code": code,
            "frames": {name: _frame_payload(f) for name, f in all_frames.items()},
            "safe_builtins": list(_SAFE_BUILTIN_NAMES),
        },
        default=str,
    )

    try:
        proc = subprocess.run(
            [node, str(_HOST_SCRIPT)],
            input=job,
            capture_output=True,
            text=True,
            timeout=_WALLCLOCK_SECONDS,
            cwd=str(_HOST_SCRIPT.parent),  # resolve node_modules/pyodide from here
        )
    except subprocess.TimeoutExpired:
        return AnalysisResult(error=f"sandbox timed out after {_WALLCLOCK_SECONDS}s")

    payload = _last_json_line(proc.stdout)
    if payload is None:
        tail = (proc.stderr or "").strip()[-300:]
        return AnalysisResult(error=f"sandbox produced no result (exit {proc.returncode}). {tail}")

    gaps = [SkillGap(**g) for g in payload.get("skill_gaps", [])]
    return AnalysisResult(
        report=payload.get("report"),
        skills_used=payload.get("skills_used", []),
        skill_gaps=gaps,
        used_inline_math=payload.get("used_inline_math", False),
        error=payload.get("error"),
    )


def _last_json_line(stdout: str) -> dict[str, Any] | None:
    """The host prints its JSON result as the final line; Pyodide banners precede it."""
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)  # type: ignore[no-any-return]
            except json.JSONDecodeError:
                continue
    return None
