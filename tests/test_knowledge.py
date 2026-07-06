"""Tests for the Insight Playbook knowledge tree + retrieval (K1)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "data-agent"))

from agent import knowledge, schema  # noqa: E402


def _clear_knowledge_caches() -> None:
    knowledge._load_pages_cached.cache_clear()  # type: ignore[attr-defined]
    knowledge._version_for.cache_clear()  # type: ignore[attr-defined]


def test_index_lists_pages_by_group() -> None:
    index = knowledge.build_index()
    assert "[presentation]" in index
    assert "[domains]" in index
    assert "when-to-visualise" in index
    assert "property-sales-overview" in index


def test_search_finds_trend_page_for_a_trend_question() -> None:
    result = knowledge.search_knowledge("trend of sale price for houses over time", limit=3)
    assert "property-sales-overview" in result or "when-to-visualise" in result


def test_search_finds_yield_page() -> None:
    result = knowledge.search_knowledge("what is the rental yield", limit=3)
    assert "yield" in result


def test_read_returns_full_body() -> None:
    body = knowledge.read_knowledge("property-rent-overview")
    assert "no suburb column" in body
    assert "total_weekly_rent" in body


def test_non_property_domain_can_be_added_without_schema_code(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "knowledge"
    page = root / "domains" / "stocks" / "overview.md"
    page.parent.mkdir(parents=True)
    page.write_text(
        """---
name: stock-prices-overview
description: Stock prices mart — ticker and daily close.
applies_to: [stock, ticker, close, volume]
---

# Stock prices

## Semantic profile
- Entity grain: `ticker`.
- Time grain: `day`.
- Measures:
  - `close_price` = adjusted close.

## Primary building block
- Table `marts.stock_prices` — one row per ticker + day.
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("KNOWLEDGE_DIR", str(root))
    _clear_knowledge_caches()
    try:
        assert "stock-prices-overview" in knowledge.build_index()
        assert "marts.stock_prices" in knowledge.search_knowledge("stock close by ticker")

        monkeypatch.setattr(
            schema,
            "get_catalog",
            lambda role="user": [
                {
                    "schema": "marts",
                    "table": "stock_prices",
                    "description": "Daily stock prices by ticker.",
                    "columns": [
                        {"name": "ticker", "type": "text", "description": None},
                        {"name": "day", "type": "date", "description": None},
                        {"name": "close_price", "type": "numeric", "description": None},
                    ],
                }
            ],
        )
        marts = schema.list_marts()
        assert "marts.stock_prices" in marts
        assert "Rent has NO suburb" not in marts
        assert "suburb values" not in marts
    finally:
        monkeypatch.delenv("KNOWLEDGE_DIR", raising=False)
        _clear_knowledge_caches()


def test_read_unknown_page_lists_available() -> None:
    body = knowledge.read_knowledge("does-not-exist")
    assert "No page named" in body


def test_version_is_stable_and_nonempty() -> None:
    v1 = knowledge.knowledge_version()
    v2 = knowledge.knowledge_version()
    assert v1 == v2
    assert v1 not in ("", "none")
