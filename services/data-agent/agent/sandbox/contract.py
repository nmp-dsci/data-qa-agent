"""The typed result a sandbox run returns.

``skills_used`` and ``skill_gaps`` are logged per run into ``app.query_runs`` so a
wrong answer in evals/diagnostics points straight at the skill responsible — the
skill is the unit of improvement. ``used_inline_math`` is a softer signal that a
skill probably *should* exist for what the model did by hand.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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
    used_inline_math: bool = False
    error: str | None = Field(default=None, description="traceback summary when the run failed")

    @property
    def ok(self) -> bool:
        return self.error is None and self.report is not None
