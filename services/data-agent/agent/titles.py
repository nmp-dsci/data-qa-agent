"""Conversation title summariser (s17 E1).

Turns a user's first question into a 3-5 word sidebar title that captures the
distinctive specifics (place names, metric, comparison) so near-identical
questions get distinguishable titles — instead of the raw question truncated to
60 chars, which produced dozens of "show me trend of sale pric…" duplicates.

Isolated on purpose: this never touches the answer path. The backend calls it
best-effort on the first answer (and a backfill script reuses it), so a slow or
missing model can only fall back to the offline heuristic, never break a chat.
"""

from __future__ import annotations

import os
import re

from .config import settings
from .provider import choose_provider

# pydantic-ai is only needed for the LLM path; guard the import so the offline
# heuristic (and its tests) run without the `llm` extra installed.
try:
    from pydantic import BaseModel
    from pydantic_ai import Agent

    class _TitleOut(BaseModel):
        """Structured output for the title agent."""

        title: str = ""

    _PYDANTIC_AI_AVAILABLE = True
except ImportError:
    _PYDANTIC_AI_AVAILABLE = False

_ENV_VAR = {"deepseek": "DEEPSEEK_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}

_TITLE_SYSTEM = (
    "You write a very short title (3-5 words) summarising a user's data question "
    "for a conversation sidebar in a NSW property-market analytics app. Capture the "
    "distinctive specifics — suburb/place names, the metric (sale price, rent, "
    "growth, yield), and any comparison — so two different questions get two "
    "different titles. Use plain words in Title Case, no quotes, no trailing "
    "punctuation, at most 6 words. Return only the title."
)

# Leading filler stripped by the offline heuristic before it keeps the core.
_FILLER_PREFIXES = (
    "show me",
    "show",
    "give me",
    "tell me",
    "what is",
    "what are",
    "what's",
    "which",
    "how many",
    "how much",
    "can you",
    "could you",
    "please",
    "list",
    "find",
    "get me",
    "i want",
    "display",
)


def _clean_title(raw: str) -> str:
    t = raw.strip().strip("\"'").rstrip(".").strip()
    words = t.split()
    if len(words) > 7:
        t = " ".join(words[:7])
    return t[:60]


def _heuristic_title(question: str) -> str:
    """Offline fallback — condense the question when no model is configured."""
    q = re.sub(r"\s+", " ", question.strip()).rstrip("?.").strip()
    low = q.lower()
    for pre in _FILLER_PREFIXES:
        if low.startswith(pre + " "):
            q = q[len(pre) :].strip()
            break
    words = q.split()
    if len(words) > 6:
        q = " ".join(words[:6])
    q = q.strip().rstrip(",")
    if not q:
        return "New conversation"
    return (q[0].upper() + q[1:])[:60]


async def _title_with_llm(question: str) -> str | None:
    if not _PYDANTIC_AI_AVAILABLE:
        return None
    selected = choose_provider(
        settings.llm_provider, settings.deepseek_api_key, settings.anthropic_api_key
    )
    if selected is None:
        return None
    provider, api_key = selected
    try:
        os.environ.setdefault(_ENV_VAR[provider], api_key)
        model_name = settings.deepseek_model if provider == "deepseek" else settings.model
        agent: Agent[None, _TitleOut] = Agent(
            f"{provider}:{model_name}",
            output_type=_TitleOut,
            system_prompt=_TITLE_SYSTEM,
        )
        run = await agent.run(f"Question: {question}")
        return _clean_title(run.output.title) or None
    except Exception as exc:  # noqa: BLE001 — never let titling break anything
        print(f"[data-agent] title generation unavailable, using heuristic: {exc}")
        return None


async def summarize_title(question: str) -> str:
    """A short sidebar title for a question — LLM when configured, else heuristic."""
    q = (question or "").strip()
    if not q:
        return "New conversation"
    llm = await _title_with_llm(q)
    return llm if llm else _heuristic_title(q)
