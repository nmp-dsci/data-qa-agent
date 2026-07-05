"""Sandbox — runs model-written pandas over a governed extract (restructure Phase A).

Phase A ships a *quick* sandbox: model code runs in a spawned subprocess with a
restricted builtins namespace, network blocked, no DB handle in scope, and CPU /
memory / wall-clock caps — enough to prove the turns/token win. Phase B swaps the
executor for Pyodide/WASM (no syscalls) behind the same ``run_code`` boundary,
without touching the skills or the agent surface.
"""

from __future__ import annotations

from .contract import AnalysisResult, SkillGap
from .runner import SandboxError, run_code

__all__ = ["run_code", "AnalysisResult", "SkillGap", "SandboxError"]
