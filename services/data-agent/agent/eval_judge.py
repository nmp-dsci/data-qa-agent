"""LLM-as-judge: one label per answer, against a reference answer (s49 M2).

The deterministic graders tell you the numbers are right (G1) and the deck is
well-formed (G3/G5). They cannot tell you whether the answer *is* the answer.
That is this module's job, and it is the only part of the scoring that is not
code.

What changed at s49, and why:

* **A reference, not a rubric of virtues.** The old ``insight-v1`` judge scored
  five abstract qualities (grounded/direct/explains-why/so-what/clear) out of
  10 with no ground truth in the prompt. That measures prose, not correctness,
  and a number out of 10 invites a threshold nobody calibrated. The judge now
  compares the answer to the golden's ``golden_answer`` — a human-written
  reference — and returns one of three labels: ``low``/``medium``/``high``.
* **A diagnosis, not just a score.** Every non-``high`` verdict names the
  stage most likely at fault (``sql``/``analysis``/``presentation``/
  ``knowledge``), which is what the offline optimiser (s49 M3) clusters on. A
  score with no cause cannot drive a fix.
* **Calibration before trust.** :func:`calibrate_judge` replays the judge over
  each golden's own ``golden_answer`` (which must come back as its ``label``,
  normally ``high``) and over every curator-written ``calibration_examples``
  entry. A run whose judge cannot reproduce known labels is recorded as
  *uncalibrated*, so its labels are read as noise rather than evidence.
* **Same family, deliberately.** The old cross-family rule (Claude answers ⇒
  DeepSeek judges) is dropped with s45: the agent runs on the Agent SDK, and a
  reference-based label is far less exposed to self-preference than a
  free-floating style score. The judge is sonnet-5 at medium effort — the same
  model as the agent, pinned so a judge upgrade is a visible change.

The rubric text is frozen and hashed into ``rubric_hash`` on every verdict: a
label is only comparable to another label produced under the same rubric.

**The judge never gates.** ``scripts/eval_run.py`` computes ``passed`` from
G1 + G5 alone (decision D2) until the pack reaches ``HOLDOUT_MIN_CASES``
goldens. The label is recorded and displayed; it does not decide.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .config import settings

# Bumped deliberately when the rubric text changes — the hash makes a silent
# edit impossible to miss, but the version is what humans talk about.
RUBRIC_VERSION = "label-v1"

LABELS = ("low", "medium", "high")
DIAGNOSES = ("sql", "analysis", "presentation", "knowledge", "none")

# pydantic-ai's AnthropicModelSettings (2.x) exposes no reasoning-effort control,
# so "medium effort" is expressed as a fixed token budget with temperature 0 and
# recorded verbatim on every verdict — a later switch to a real effort knob is
# then a visible change in the stored `effort`, not a silent one.
JUDGE_MAX_TOKENS = 1024
JUDGE_EFFORT = f"medium (temperature=0, max_tokens={JUDGE_MAX_TOKENS})"

LABEL_RUBRIC = """\
You grade ONE answer produced by a data assistant against a REFERENCE ANSWER
written by a human expert. The reference is the truth: where they disagree, the
answer is wrong. You are not rewarding style, length, or enthusiasm.

Return one LABEL:

- "high"   — every material claim agrees with the reference: the headline
             number(s) match (small rounding or wording differences are fine),
             the direction/ranking is the same, and nothing important the
             reference states is missing or contradicted.
- "medium" — broadly the right shape but materially incomplete or partly wrong:
             a headline number is off beyond rounding, a named entity or period
             differs, or the reference's main point is present only by
             implication.
- "low"    — the answer does not answer the question, contradicts the
             reference, is unsupported by the data shown, or reports an error.

Then return one DIAGNOSIS — the single stage most likely at fault. Use "none"
when the label is "high".

- "sql"          — the wrong rows were fetched: wrong filter, period, grain, or
                   entity, so the numbers were never going to be right.
- "analysis"     — the right rows, the wrong maths: bad aggregation, an average
                   of averages, a growth/rolling calculation done by hand.
- "presentation" — the numbers are right but the delivery fails: the headline is
                   buried or absent, the deck has no chart/table carrying it, or
                   the KPI shown disagrees with the text.
- "knowledge"    — a domain convention was missed (e.g. rent is weighted by
                   bond count, not a mean of medians; a band order; a mart's
                   coverage window).

Judge only what you are given. If the extracted values are absent, do not assume
they were wrong — weigh the answer text and the deck outline.

Return ONLY a JSON object, no prose, no code fence:
{"label": "low|medium|high", "diagnosis": "sql|analysis|presentation|knowledge|none",
 "reason": "one sentence naming the single decisive difference from the reference"}
"""


def rubric_hash() -> str:
    """Content hash of the frozen rubric — recorded on every verdict."""
    return "jr-" + hashlib.sha256(LABEL_RUBRIC.encode("utf-8")).hexdigest()[:8]


def judge_choice() -> tuple[str, str]:
    """(transport, model) for the judge — always sonnet-5, or ("", "").

    Two transports reach the same model, and which one is available differs by
    environment rather than by choice:

    * ``api`` — ``ANTHROPIC_API_KEY`` via pydantic-ai, the deterministic path
      (temperature 0). Preferred whenever a key exists.
    * ``sdk`` — the Claude Agent SDK's CLI subprocess on a subscription
      (``CLAUDE_CODE_OAUTH_TOKEN``), which is how this stack is authenticated in
      local dev (s45). Without this fallback the judge would be unrunnable on
      exactly the machine the loop is developed on, which is how judges quietly
      become decorative.

    Returns empty strings when neither is configured, so the caller reports the
    gap explicitly rather than grading with a stub.
    """
    if settings.anthropic_api_key:
        return "api", settings.sdk_model
    if settings.claude_code_oauth_token:
        return "sdk", settings.sdk_model
    return "", ""


def judge_model() -> str:
    """The model that labels answers (for callers that only need the name)."""
    return judge_choice()[1]


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


def _normalise(parsed: dict[str, Any]) -> dict[str, Any]:
    """Coerce a reply to the rubric's vocabulary.

    A label outside the three permitted values is *not* silently mapped to a
    middle value: it becomes ``low`` with the raw text kept, because an
    unparseable verdict is evidence about the judge, not about the answer.
    """
    label = str(parsed.get("label", "")).strip().lower()
    diagnosis = str(parsed.get("diagnosis", "")).strip().lower()
    out: dict[str, Any] = {
        "label": label if label in LABELS else "low",
        "diagnosis": diagnosis if diagnosis in DIAGNOSES else "none",
        "reason": str(parsed.get("reason", ""))[:400],
        "rubric_version": RUBRIC_VERSION,
        "rubric_hash": rubric_hash(),
    }
    if label not in LABELS:
        out["unparsed_label"] = label[:80]
    # A "high" that still names a fault, or a non-high with "none", is the model
    # contradicting itself; keep the label (it is the graded quantity) and make
    # the diagnosis consistent so downstream clustering isn't polluted.
    if out["label"] == "high":
        out["diagnosis"] = "none"
    elif out["diagnosis"] == "none":
        out["diagnosis"] = "analysis"
    return out


def _skipped(reason: str) -> dict[str, Any]:
    """A verdict that says "no judgement", never a default label."""
    return {
        "skipped": True,
        "reason": reason,
        "label": None,
        "diagnosis": None,
        "model": judge_model(),
        "effort": JUDGE_EFFORT,
        "rubric_version": RUBRIC_VERSION,
        "rubric_hash": rubric_hash(),
    }


def _prompt(
    *,
    question: str,
    golden_answer: str,
    golden_values: str,
    answer: str,
    deck_outline: str,
    g1: Any,
) -> str:
    """The graded material, in a fixed order so the prompt is deterministic."""
    reference = golden_answer or "(none provided — judge the answer on the data it shows)"
    blocks = [f"QUESTION:\n{question}", f"REFERENCE ANSWER (the truth):\n{reference}"]
    if golden_values:
        blocks.append(f"REFERENCE VALUES (rows the reference was written from):\n{golden_values}")
    blocks.append(f"ANSWER TO GRADE:\n{answer}")
    if deck_outline:
        blocks.append(f"DECK THE USER RECEIVED:\n{deck_outline}")
    if g1 is not None:
        blocks.append(f"DETERMINISTIC EXTRACTION SCORE (G1, 0-1, already graded):\n{g1}")
    return "\n\n".join(blocks)


async def _sdk_text(prompt: str, model: str) -> str:
    """One single-turn judged call through the Claude Agent SDK (subscription).

    No tools, no workspace, one turn: this is a classification, not an agent
    run. ``cli_env()`` is reused verbatim so the CLI subprocess authenticates
    exactly the way the agent's own runs do — blanking any provider key so a
    stray one cannot silently switch the judge to per-token billing.
    """
    import importlib  # noqa: PLC0415

    from .sdk_agent import cli_env  # noqa: PLC0415

    sdk = importlib.import_module("claude_agent_sdk")
    options = sdk.ClaudeAgentOptions(
        model=model,
        system_prompt=LABEL_RUBRIC,
        max_turns=1,
        tools=[],
        allowed_tools=[],
        env=cli_env(),
    )
    chunks: list[str] = []
    async for message in sdk.query(prompt=prompt, options=options):
        for block in getattr(message, "content", None) or []:
            text = getattr(block, "text", None)
            if text:
                chunks.append(str(text))
    return "\n".join(chunks)


async def _run_judge(prompt: str) -> dict[str, Any]:
    """One judged call, or a ``skipped`` verdict. Never raises."""
    transport, model = judge_choice()
    if not model:
        return _skipped("no judge configured (set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN)")
    if transport == "sdk":
        try:
            verdict = _normalise(_extract_json(await _sdk_text(prompt, model)))
        except Exception as exc:  # noqa: BLE001 - a judge failure is data, not a crash
            return _skipped(f"judge call failed: {exc}")
        verdict["model"] = model
        verdict["transport"] = "sdk"
        # The CLI decides its own sampling, so the API path's determinism claim
        # would be false here; recorded as what it is rather than assumed.
        verdict["effort"] = "medium (agent SDK defaults, single turn)"
        return verdict
    try:
        # Imported lazily: the judge is the only caller, and a run without a
        # judge must not pay the agent-framework import cost.
        import os  # noqa: PLC0415

        from pydantic_ai import Agent  # noqa: PLC0415

        from .model_factory import build_model, model_settings, run_with_policy  # noqa: PLC0415

        assert settings.anthropic_api_key  # guaranteed by judge_choice()
        os.environ.setdefault("ANTHROPIC_API_KEY", settings.anthropic_api_key)
        judge: Agent[None, str] = Agent(
            build_model("anthropic", model),
            system_prompt=LABEL_RUBRIC,
            # Deterministic: a judge that wobbles between runs turns every
            # comparison into noise. Temperature 0 plus a fixed budget is the
            # whole of "medium effort" on this pydantic-ai version.
            model_settings={**model_settings(), "temperature": 0.0, "max_tokens": JUDGE_MAX_TOKENS},
        )
        # s32 W1: the judge retries too. A 429 mid-pack used to record a
        # "judge call failed" verdict for that case, which is a fact about the
        # provider, not about the answer.
        result, _attempts = await run_with_policy(lambda: judge.run(prompt), label="eval judge")
        verdict = _normalise(_extract_json(str(result.output)))
    except Exception as exc:  # noqa: BLE001 - a judge failure is data, not a crash
        return _skipped(f"judge call failed: {exc}")
    verdict["model"] = model
    verdict["transport"] = "api"
    verdict["effort"] = JUDGE_EFFORT
    return verdict


async def judge_answer(
    *,
    question: str,
    golden_answer: str = "",
    golden_values: str = "",
    answer: str,
    deck_outline: str = "",
    g1: Any = None,
) -> dict[str, Any]:
    """Label one answer against its golden's reference answer.

    Returns ``{label, diagnosis, reason, model, effort, rubric_hash,
    rubric_version}``, or a ``skipped`` verdict (``label: None``) when no judge
    is configured or the call failed — never a fabricated label.
    """
    return await _run_judge(
        _prompt(
            question=question,
            golden_answer=golden_answer,
            golden_values=golden_values,
            answer=answer,
            deck_outline=deck_outline,
            g1=g1,
        )
    )


async def calibrate_judge(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Can the judge reproduce labels we already know?

    For each case: its ``golden_answer`` must come back as its ``label``
    (normally ``high``), and every ``calibration_examples`` entry
    (``{label, answer}``, curator-written) must come back as its own label. One
    disagreement fails the whole calibration — the judge either reproduces known
    labels or its labels on unknown answers mean nothing.

    Runs once per ``make eval`` (see ``scripts/eval_run.py``); the verdict lands
    on ``eval_runs.totals.judge_calibration`` and on every
    ``eval_results.judge.calibrated``.
    """
    results: list[dict[str, Any]] = []
    for case in cases:
        key = str(case.get("case_key") or "?")
        question = str(case.get("question") or "")
        reference = str(case.get("golden_answer") or "")
        if not reference:
            continue
        probes: list[tuple[str, str, str]] = [
            ("golden_answer", str(case.get("label") or "high"), reference)
        ]
        for i, ex in enumerate(case.get("calibration_examples") or []):
            if not isinstance(ex, dict):
                continue
            expected = str(ex.get("label") or "").strip().lower()
            text = str(ex.get("answer") or "")
            if expected in LABELS and text:
                probes.append((f"example[{i}]", expected, text))
        for probe, expected, text in probes:
            verdict = await judge_answer(question=question, golden_answer=reference, answer=text)
            got = verdict.get("label")
            results.append(
                {
                    "case_key": key,
                    "probe": probe,
                    "expected": expected,
                    "got": got,
                    "agreed": got == expected,
                    **({"error": verdict["reason"]} if verdict.get("skipped") else {}),
                }
            )
    agreed = [r for r in results if r["agreed"]]
    return {
        # No probes at all is *not* calibrated: a pack with no reference answers
        # has produced no evidence that the judge works, and reporting True
        # there would let an unlabelled pack claim a calibrated judge.
        "calibrated": bool(results) and len(agreed) == len(results),
        "probes": len(results),
        "agreed": len(agreed),
        "results": results,
        "model": judge_model(),
        "effort": JUDGE_EFFORT,
        "rubric_version": RUBRIC_VERSION,
        "rubric_hash": rubric_hash(),
    }
