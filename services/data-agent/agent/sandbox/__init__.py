"""Sandbox — runs model-written pandas over a governed extract (restructure).

Two executors share one ``run_code`` boundary, selected by ``settings.sandbox_runtime``:

* ``subprocess`` (Phase A, default) — a spawned process with a restricted builtins
  namespace, network blocked, no DB handle in scope, and CPU / wall-clock caps.
  Zero extra deps; the default so host unit tests run without Node.
* ``pyodide`` (Phase B) — model code runs in Pyodide (CPython in WebAssembly): no
  syscalls, no host filesystem, no network. Needs Node + the bundled pyodide in
  the image; docker-compose turns it on.

Both use the same skills and return the same ``AnalysisResult``, so the agent
surface is identical — this only swaps the isolation boundary.
"""

from __future__ import annotations

import pandas as pd

from ..config import settings
from .contract import AnalysisResult, SkillGap
from .errors import explain_sandbox_error
from .runner import SandboxError
from .runner import run_code as _subprocess_run_code

__all__ = ["run_code", "AnalysisResult", "SkillGap", "SandboxError", "explain_sandbox_error"]


def run_code(
    code: str,
    df: pd.DataFrame | None = None,
    *,
    frames: dict[str, pd.DataFrame] | None = None,
) -> AnalysisResult:
    """Execute model-written ``code`` over the extract, on the configured runtime."""
    if settings.sandbox_runtime == "pyodide":
        from .pyodide_runner import run_code as _pyodide_run_code

        return _pyodide_run_code(code, df, frames=frames)
    return _subprocess_run_code(code, df, frames=frames)
