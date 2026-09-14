"""The typed result a sandbox run returns.

``skills_used`` and ``skill_gaps`` are logged per run into ``app.query_runs`` so a
wrong answer in evals/diagnostics points straight at the skill responsible — the
skill is the unit of improvement. ``used_inline_math`` is a softer signal that a
skill probably *should* exist for what the model did by hand.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# s49 M0: how much of a run's printed output travels back with the result.
# The model prints to debug its own pandas — that output is the single most
# useful thing in a trace when an analysis silently produces the wrong number —
# but it is model-controlled and unbounded, so it is capped rather than trusted.
# Both executors enforce this same number (subprocess and Pyodide), so a trace
# does not change shape when SANDBOX_RUNTIME does.
STDOUT_CAP = 8192


def cap_stdout(text: str) -> str:
    """``text`` capped at :data:`STDOUT_CAP`, saying so when it was cut."""
    if len(text) <= STDOUT_CAP:
        return text
    return text[:STDOUT_CAP] + f"\n…[stdout truncated, {len(text) - STDOUT_CAP} chars dropped]"


class SkillGap(BaseModel):
    """A piece of analysis no skill covered yet — feeds the authoring backlog."""

    need: str = Field(description="what a future skill should do, e.g. 'seasonality_adjust'")
    why: str = Field(default="", description="why no existing skill fit")


class AnalysisResult(BaseModel):
    """What ``run_code`` returns to the agent tool."""

    report: dict[str, Any] | None = Field(
        default=None,
        description="the narrative report dict (build_report output), or None on error",
    )
    skills_used: list[str] = Field(default_factory=list)
    skill_gaps: list[SkillGap] = Field(default_factory=list)
    frames: list[dict[str, Any]] = Field(
        default_factory=list,
        description="named derived frames the run built + fed to a skill (the "
        "enrichment stage: {name, columns, rows, shape}); for the Golden builder",
    )
    used_inline_math: bool = False
    stdout: str = Field(
        default="",
        description="whatever the model's code printed, capped at STDOUT_CAP chars",
    )
    error: str | None = Field(default=None, description="traceback summary when the run failed")

    @property
    def ok(self) -> bool:
        return self.error is None and self.report is not None
