"""Tests for the Insight Playbook knowledge tree + retrieval (K1)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "data-agent"))

from agent import knowledge  # noqa: E402


def test_index_lists_pages_by_group() -> None:
    index = knowledge.build_index()
    assert "[presentation]" in index
    assert "[analysis]" in index
    assert "[domains]" in index
    assert "trend-charts" in index
    assert "growth-rates" in index


def test_search_finds_trend_page_for_a_trend_question() -> None:
    result = knowledge.search_knowledge("trend of sale price for houses over time", limit=3)
    assert "trend-charts" in result


def test_search_finds_yield_page() -> None:
    result = knowledge.search_knowledge("what is the rental yield", limit=3)
    assert "yield" in result


def test_read_returns_full_body() -> None:
    body = knowledge.read_knowledge("growth-rates")
    assert "6-month rolling" in body
    assert "growth_rate" in body


def test_read_unknown_page_lists_available() -> None:
    body = knowledge.read_knowledge("does-not-exist")
    assert "No page named" in body


def test_version_is_stable_and_nonempty() -> None:
    v1 = knowledge.knowledge_version()
    v2 = knowledge.knowledge_version()
    assert v1 == v2
    assert v1 not in ("", "none")
