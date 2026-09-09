"""Demo-mode chat: replay recorded agent runs (s38 P1).

Prod-as-demo never calls the LLM. Instead, showcase questions are asked once in
dev (full agent + DeepSeek), exported from ``app.query_runs`` by
``scripts/demo_pack.py`` into ``app/demo_pack/*.json``, and shipped inside the
backend image. At runtime this module:

- loads the pack into memory at first use (no DB touch — chat replay stays
  instant while Aurora resumes from auto-pause, s29);
- resolves a visitor's free-text question to the nearest recorded answer with
  ``difflib`` (decision D1: chips + fuzzy match). Below the threshold the reply
  lists the available questions instead of pretending to know;
- synthesizes the same SSE frames a live run streams (``progress`` steps from
  the recorded trace, a ``plan`` frame, one ``page`` frame per stored page) with
  condensed pacing, so the visitor watches the agent "work" in a few seconds
  instead of the ~95s a live prod answer takes.

The replayed result flows through the normal persistence path in
``routers/ask.py`` — demo runs land in ``app.query_runs`` with
``engine='demo_replay'``, so the audit surfaces and the analytics tab count
them like any other traffic.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import settings

PACK_DIR = Path(__file__).resolve().parent / "demo_pack"

# Condensed replay pacing: a full answer plays out in roughly 3-6s.
_PROGRESS_DELAY_S = 0.28
_PAGE_DELAY_S = 0.45
_MAX_PROGRESS_STEPS = 12

# Trace step kinds -> the friendly action label the chat flight strip shows.
# The recorded trace is the persisted diagnostic trace (kind/status/...), not
# the live progress feed, so replay derives a readable step list from it.
_STEP_ACTIONS = {
    "system": "briefing the agent",
    "user": "reading the question",
    "model": "thinking",
    "tool_return": "query returned",
    "analysis": "analysing the extract",
    "decision_log": "recording decisions",
    "template_pick": "choosing a layout",
    "object_build": "building charts",
    "page_compose": "composing the report",
    "fallback": "recovering",
}


@dataclass(frozen=True)
class DemoAnswer:
    id: str
    question: str
    answer: str
    sql: str | None
    engine: str
    row_count: int
    latency_ms: int | None
    steps: tuple[dict[str, Any], ...]
    report: dict[str, Any] | None
    pages: tuple[dict[str, Any], ...]
    # s46: the Slides/Sheets artifact recorded with this answer. Demo runs no
    # agent and holds no Google credential — replay only hands back the URLs
    # the dev run already produced and shared read-only.
    # Defaulted: a pack entry recorded before s46 simply has no artifact, which
    # is a normal state rather than a malformed file.
    artifact: dict[str, Any] | None = None


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


# Small English stop-word set: matching should ride on content words, not on
# "the/what/which" scaffolding two unrelated questions share.
_STOP_WORDS = frozenset(
    "a an and are be by for from had has have how in is it me my of on or over "
    "past per s show t that the this to was were what where which with year years last".split()
)


def _content_words(text: str) -> frozenset[str]:
    # Light stemming (strip a trailing 's') so "yields" meets "yield".
    return frozenset(
        w[:-1] if w.endswith("s") and len(w) > 3 else w
        for w in _norm(text).split()
        if w not in _STOP_WORDS
    )


def _score(asked: str, candidate: str) -> float:
    """Similarity in [0,1]: sequence ratio blended with content-word overlap.

    ``SequenceMatcher`` alone scores unrelated short sentences surprisingly high
    (~0.45) on shared function words; Jaccard over stop-word-filtered, lightly
    stemmed tokens pulls those back down while keeping true paraphrases high.
    """
    seq = difflib.SequenceMatcher(None, _norm(asked), _norm(candidate)).ratio()
    a, b = _content_words(asked), _content_words(candidate)
    jaccard = len(a & b) / len(a | b) if (a or b) else 0.0
    return 0.4 * seq + 0.6 * jaccard


@lru_cache(maxsize=1)
def load_pack() -> tuple[DemoAnswer, ...]:
    """Parse every pack file once. A malformed file is skipped loudly rather
    than taking the whole demo down — the pack is authored content, and one bad
    export must not turn into an outage."""
    answers: list[DemoAnswer] = []
    for path in sorted(PACK_DIR.glob("*.json")):
        try:
            raw = json.loads(path.read_text())
            report = raw.get("report")
            pages = raw.get("pages") or (report or {}).get("pages") or []
            artifact = raw.get("artifact") or (report or {}).get("artifact")
            answers.append(
                DemoAnswer(
                    id=raw.get("id") or path.stem,
                    question=str(raw["question"]),
                    answer=str(raw.get("answer", "")),
                    sql=raw.get("sql"),
                    engine=str(raw.get("engine", "deepseek")),
                    row_count=int(raw.get("row_count", 0)),
                    latency_ms=raw.get("latency_ms"),
                    steps=tuple(raw.get("steps") or []),
                    report=report,
                    pages=tuple(pages),
                    artifact=artifact,
                )
            )
        except Exception as exc:  # noqa: BLE001 — skip the bad file, keep the demo up
            print(f"[demo_replay] skipping malformed pack file {path.name}: {exc}")
    return tuple(answers)


def pack_index() -> list[dict[str, str]]:
    """The chip rail: what a visitor can ask, in pack order."""
    return [{"id": a.id, "question": a.question} for a in load_pack()]


def resolve(question: str) -> tuple[DemoAnswer | None, float]:
    """Nearest recorded answer for a free-text question (D1).

    Exact (normalised) matches and chip ids score 1.0; otherwise the best
    ``difflib`` ratio across the pack. The caller applies the threshold.
    """
    pack = load_pack()
    if not pack:
        return None, 0.0
    asked = _norm(question)
    best: DemoAnswer | None = None
    best_score = 0.0
    for ans in pack:
        if asked == _norm(ans.question) or question.strip() == ans.id:
            return ans, 1.0
        score = _score(question, ans.question)
        if score > best_score:
            best, best_score = ans, score
    return best, best_score


def _miss_result(question: str) -> dict[str, Any]:
    """No recorded answer is close enough: say so, honestly, and point at the
    chips. Shaped like a normal degraded-free result so persistence and the UI
    need no special case."""
    lines = "\n".join(f"- {a.question}" for a in load_pack()[:8])
    answer = (
        "This portfolio demo replays answers that were recorded from the full "
        "live build, and none of them are close enough to that question.\n\n"
        "Here are some it can answer:\n" + (lines or "- (the demo pack is empty)")
    )
    return {
        "answer": answer,
        "engine": "demo_replay",
        "row_count": 0,
        "steps": [],
        "demo_matched_question": None,
        "demo_miss": True,
    }


def result_for(question: str) -> dict[str, Any]:
    """The /ask result dict for a demo question — same shape ask_agent returns."""
    ans, score = resolve(question)
    if ans is None or score < settings.demo_fuzzy_threshold:
        return _miss_result(question)
    exact = score >= 0.999
    return {
        "answer": ans.answer,
        "sql": ans.sql,
        "engine": "demo_replay",
        "row_count": ans.row_count,
        "steps": list(ans.steps),
        "report": ans.report,
        "pages": list(ans.pages) or None,
        "artifact": ans.artifact,
        # The note the chat bubble renders when the reply is a near-match, not
        # the literal question asked ("closest recorded answer").
        "demo_matched_question": None if exact else ans.question,
    }


def _progress_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for step in steps:
        kind = str(step.get("kind", ""))
        action = _STEP_ACTIONS.get(kind)
        if action is None:
            continue
        detail = step.get("name") or step.get("to") or ""
        out.append({"n": len(out) + 1, "action": action, "detail": str(detail)[:80]})
    if len(out) > _MAX_PROGRESS_STEPS:
        # Keep the head and tail — the interesting parts — and elide the middle.
        head = out[: _MAX_PROGRESS_STEPS // 2]
        tail = out[-(_MAX_PROGRESS_STEPS - len(head)) :]
        out = head + tail
        for i, step in enumerate(out):
            step["n"] = i + 1
    return out


def _plan_slots(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    for i, page in enumerate(pages):
        slots.append(
            {
                "index": i + 1,
                "kind": page.get("kind", page.get("template", "summary")),
                "template": page.get("template", "two-col"),
                "status": "building" if i == 0 else "planned",
            }
        )
    return slots


async def replay_events(result: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """Yield the SSE event dicts a live ``ask_agent_stream`` would produce,
    paced so the visitor watches the recorded run happen. The caller relays
    them exactly like live frames and then persists ``result`` as usual."""
    pages = list(result.get("pages") or [])
    steps = _progress_steps(list(result.get("steps") or []))

    if pages:
        yield {"event": "plan", "data": {"event": "plan", "pages": _plan_slots(pages)}}
    for step in steps:
        yield {"event": "progress", "data": step}
        await asyncio.sleep(_PROGRESS_DELAY_S)
    for i, page in enumerate(pages):
        await asyncio.sleep(_PAGE_DELAY_S)
        yield {
            "event": "page",
            "data": {
                "event": "page",
                "index": i + 1,
                "kind": page.get("kind", page.get("template", "summary")),
                "status": "complete",
                "page": page,
            },
        }
