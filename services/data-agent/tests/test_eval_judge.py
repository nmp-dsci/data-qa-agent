"""The label judge: parsing, normalisation, and calibration (s49 M2).

No model is called here. Every test drives ``_normalise``/``_extract_json``
directly or monkeypatches the one call site, because the properties worth
pinning are about what the judge does with a reply — including a bad one — not
about whether Anthropic is up.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent import eval_judge
from agent.eval_judge import (
    RUBRIC_VERSION,
    _extract_json,
    _normalise,
    calibrate_judge,
    rubric_hash,
)


def test_rubric_hash_is_stable_and_versioned() -> None:
    assert rubric_hash().startswith("jr-")
    assert rubric_hash() == rubric_hash()
    assert RUBRIC_VERSION == "label-v1"


def test_extract_json_handles_fences_and_chatter() -> None:
    assert _extract_json('```json\n{"label": "high"}\n```')["label"] == "high"
    assert _extract_json('Here you go: {"label": "low"} — hope that helps')["label"] == "low"
    with pytest.raises(ValueError):
        _extract_json("no object here")


def test_normalise_keeps_the_vocabulary() -> None:
    out = _normalise({"label": " HIGH ", "diagnosis": "SQL", "reason": "fine"})
    assert out["label"] == "high"
    # A "high" cannot also blame a stage; the label is the graded quantity.
    assert out["diagnosis"] == "none"
    assert out["rubric_version"] == RUBRIC_VERSION


def test_normalise_refuses_to_invent_a_middle_label() -> None:
    """An unparseable verdict is evidence about the judge, not the answer.

    Mapping "excellent" to ``medium`` would quietly turn a broken judge into a
    plausible-looking score, so it lands on ``low`` with the raw text kept.
    """
    out = _normalise({"label": "excellent", "diagnosis": "vibes"})
    assert out["label"] == "low"
    assert out["unparsed_label"] == "excellent"
    # A non-high with no usable diagnosis still names a stage, so the optimiser's
    # clustering never has to special-case "none on a failing case".
    assert out["diagnosis"] == "analysis"


def _fake_judge(labels: dict[str, str]) -> Any:
    """A judge that returns whatever label the answer text maps to."""

    async def judge_answer(*, answer: str, **_: Any) -> dict[str, Any]:
        return {"label": labels.get(answer.strip(), "low"), "diagnosis": "none", "reason": ""}

    return judge_answer


CASES = [
    {
        "case_key": "c1",
        "question": "what?",
        "golden_answer": "the reference",
        "label": "high",
        "calibration_examples": [
            {"label": "medium", "answer": "roughly right"},
            {"label": "low", "answer": "wrong"},
        ],
    }
]


def test_calibration_passes_when_every_probe_agrees(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        eval_judge,
        "judge_answer",
        _fake_judge({"the reference": "high", "roughly right": "medium", "wrong": "low"}),
    )
    out = asyncio.run(calibrate_judge(CASES))
    assert out["calibrated"] is True
    assert (out["probes"], out["agreed"]) == (3, 3)


def test_one_disagreement_fails_the_whole_calibration(monkeypatch: Any) -> None:
    """Partial agreement is not partial trust — the labels are one scale."""
    monkeypatch.setattr(
        eval_judge,
        "judge_answer",
        _fake_judge({"the reference": "high", "roughly right": "high", "wrong": "low"}),
    )
    out = asyncio.run(calibrate_judge(CASES))
    assert out["calibrated"] is False
    assert [r["agreed"] for r in out["results"]] == [True, False, True]


def test_no_reference_answers_is_not_calibrated(monkeypatch: Any) -> None:
    """Zero probes means zero evidence; reporting True there would let an
    unlabelled pack claim a working judge."""
    monkeypatch.setattr(eval_judge, "judge_answer", _fake_judge({}))
    out = asyncio.run(calibrate_judge([{"case_key": "c1", "question": "q"}]))
    assert out["calibrated"] is False
    assert out["probes"] == 0
