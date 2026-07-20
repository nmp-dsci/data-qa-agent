"""LLM-as-judge for the insight half of G3 (s24 M2).

The deterministic graders can tell you the numbers are right and the report has
the right shape. They cannot tell you whether the answer actually *says*
anything — whether it is grounded, direct, and leaves the reader knowing what to
do. That is the judge's job, and it is the only part of the scoring that is not
code.

Three disciplines make a judge trustworthy rather than decorative:

* **Frozen rubric.** ``INSIGHT_RUBRIC`` is versioned text and hashed into
  ``judge_prompt_hash`` on every run. A score is only comparable to another
  score produced under the same rubric, so the rubric cannot drift silently.
* **Cross-family.** The judge must not belong to the same model family as the
  agent it grades, or it rewards its own habits (self-preference bias). With
  DeepSeek answering, Claude judges.
* **Calibration before trust.** Insight is scored and reported but does not gate
  a case on its own (``scripts/eval_run.py``) until its agreement with a human
  rater has been measured, not assumed — that calibration step is not yet
  built.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .config import settings

# Bumped deliberately when the rubric text changes — the hash makes a silent
# edit impossible to miss, but the version is what humans talk about.
RUBRIC_VERSION = "insight-v1"

INSIGHT_RUBRIC = """\
You are grading the INSIGHT QUALITY of a data assistant's answer to a business
question. You are not checking arithmetic — the numbers have already been graded
deterministically. Judge only whether this answer is worth reading.

Score five criteria, each 0, 1, or 2:

1. GROUNDED — every claim traces to the data shown. 0 = invents or contradicts;
   1 = mostly grounded with vague or unsupported edges; 2 = fully grounded.
2. DIRECT — it answers the question actually asked, first. 0 = answers a
   different question or buries it; 1 = answers it eventually; 2 = leads with it.
3. EXPLAINS WHY — it interprets movement rather than narrating numbers.
   0 = pure readout; 1 = gestures at a reason; 2 = a real, data-supported reason.
4. SO-WHAT — it tells the reader what this means for a decision. 0 = absent;
   1 = generic; 2 = specific and actionable.
5. CLEAR — plain language, no jargon dumps, no padding. 0 = hard to follow;
   1 = readable but bloated; 2 = tight and clear.

Be strict. A fluent answer that merely restates the chart is a 1 on EXPLAINS WHY
and a 0 on SO-WHAT. Reserve 2s for answers a domain expert would send onward
unedited.

Return ONLY a JSON object, no prose, no code fence:
{"grounded": n, "direct": n, "explains_why": n, "so_what": n, "clear": n,
 "total": n, "reasoning": "one sentence naming the single biggest weakness"}
where total is the sum out of 10.
"""


def rubric_hash() -> str:
    """Content hash of the frozen rubric — recorded on every eval_run."""
    return "jr-" + hashlib.sha256(INSIGHT_RUBRIC.encode("utf-8")).hexdigest()[:8]


def judge_model() -> str:
    """The model that grades insight.

    Deliberately *not* the agent's own model: a judge from the same family
    rewards its own phrasing. When no cross-family key is configured the caller
    is told so explicitly rather than quietly grading with the agent itself.
    """
    if settings.anthropic_api_key and settings.llm_provider != "anthropic":
        return settings.model
    return ""


def _extract_json(text: str) -> dict[str, Any]:
    """Pull the JSON object out of a model reply that may be fenced or chatty."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    raw = fenced.group(1) if fenced else text
    if not fenced:
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object in judge reply")
        raw = raw[start : end + 1]
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("judge reply was not an object")
    return parsed


_CRITERIA = ("grounded", "direct", "explains_why", "so_what", "clear")


def _normalise(parsed: dict[str, Any]) -> dict[str, Any]:
    """Clamp to the rubric's range and recompute the total ourselves.

    Models are perfectly capable of returning a total that does not match their
    own criteria, or a 7 on a 0-2 scale. Trusting the parts and recomputing the
    whole keeps one bad reply from silently inflating a run.
    """
    scores: dict[str, int] = {}
    for name in _CRITERIA:
        try:
            value = int(parsed.get(name, 0))
        except (TypeError, ValueError):
            value = 0
        scores[name] = max(0, min(2, value))
    total = sum(scores.values())
    return {
        **scores,
        "total": total,
        "max": 2 * len(_CRITERIA),
        "reasoning": str(parsed.get("reasoning", ""))[:400],
        "rubric_version": RUBRIC_VERSION,
        "judge_prompt_hash": rubric_hash(),
    }


async def judge_insight(*, question: str, answer: str, evidence: str = "") -> dict[str, Any]:
    """Score one answer's insight quality out of 10.

    Returns a ``skipped`` verdict rather than raising when no cross-family judge
    is configured, so a run without an Anthropic key still produces G1/G2/G3
    structural scores instead of failing outright — with the gap recorded, not
    hidden.
    """
    model = judge_model()
    if not model:
        return {
            "skipped": True,
            "reason": "no cross-family judge configured (set ANTHROPIC_API_KEY)",
            "total": None,
            "max": 2 * len(_CRITERIA),
            "rubric_version": RUBRIC_VERSION,
            "judge_prompt_hash": rubric_hash(),
        }

    prompt = (
        f"QUESTION:\n{question}\n\n"
        f"ANSWER TO GRADE:\n{answer}\n\n"
        f"{('EVIDENCE THE ANSWER HAD ACCESS TO:' + chr(10) + evidence) if evidence else ''}"
    )
    try:
        # Imported lazily: the judge is the only caller, and a run without a
        # judge must not pay the agent-framework import cost.
        import os

        from pydantic_ai import Agent

        os.environ.setdefault("ANTHROPIC_API_KEY", settings.anthropic_api_key)
        judge: Agent[None, str] = Agent(f"anthropic:{model}", system_prompt=INSIGHT_RUBRIC)
        result = await judge.run(prompt)
        verdict = _normalise(_extract_json(str(result.output)))
    except Exception as exc:  # noqa: BLE001 - a judge failure is data, not a crash
        return {
            "skipped": True,
            "reason": f"judge call failed: {exc}",
            "total": None,
            "max": 2 * len(_CRITERIA),
            "rubric_version": RUBRIC_VERSION,
            "judge_prompt_hash": rubric_hash(),
        }
    verdict["judge_model"] = model
    return verdict
