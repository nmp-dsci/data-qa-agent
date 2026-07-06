"""Insight-structure skills — the report shape, headlines, and insights.

The sandbox holds real chart objects (not id refs), so these skills assemble the
narrative directly into the same report dict the server produces today
(``element_id`` anchors, headline/insight/profile shape). The sandbox adapter
then merges in the governed ``queries`` and ``knowledge_version`` around this.

Replaces knowledge pages: report-structure.md, what-makes-an-insight.md,
related-metrics.md.
"""

from __future__ import annotations

from typing import Any

from . import skill


@skill
def make_insight(
    heading: str,
    body: str,
    *,
    query_refs: list[str] | None = None,
    chart: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One insight card: a claim (heading) + why it matters (body), optional chart.

    A good insight says something the table doesn't (a trend, a comparison, a
    ratio) — not a restated row. ``chart`` is an inline Vega spec (e.g. from
    ``comparison_chart``); leave it None to inherit the report's main chart.
    """
    return {
        "heading": heading,
        "body": body,
        "query_refs": list(query_refs or []),
        "chart": chart,
    }


@skill
def related_metrics(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark a set of headline tiles as related context (not the asked-for metric).

    Pass ``[{"label":..., "value":..., "basis":...}, ...]``; each is flagged
    ``related=True`` so the frontend renders it as supporting context beside the
    primary headline (see related-metrics.md).
    """
    out: list[dict[str, Any]] = []
    for it in items:
        tile = dict(it)
        tile["related"] = True
        out.append(tile)
    return out


def _headline(tile: dict[str, Any], i: int) -> dict[str, Any]:
    return {
        "element_id": f"headline:{i}",
        "label": tile.get("label", ""),
        "value": tile.get("value", ""),
        "basis": tile.get("basis", ""),
        "related": bool(tile.get("related", False)),
        "query_ref": tile.get("query_ref"),
    }


@skill
def build_report(
    *,
    summary: str,
    headlines: list[dict[str, Any]] | None = None,
    insights: list[dict[str, Any]] | None = None,
    profiles: list[dict[str, Any]] | None = None,
    main_chart: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the narrative into the app's report shape (minus queries/version).

    Produces the same element-shape the server assembles today: a one-sentence
    ``summary``, headline tiles, 2-4 insight cards, an optional profile section,
    and the primary ``main_chart``. The sandbox adapter adds the governed
    ``queries`` and ``knowledge_version`` around this.
    """
    headline_tiles = [_headline(h, i) for i, h in enumerate(headlines or [])]
    insight_cards = [
        {
            "element_id": f"insight:{i}",
            "heading": ins.get("heading", ""),
            "body": ins.get("body", ""),
            "query_refs": ins.get("query_refs", []),
            # An insight that repeats the main chart shouldn't render it twice.
            "chart": None if ins.get("chart") == main_chart else ins.get("chart"),
        }
        for i, ins in enumerate(insights or [])
    ]
    profile_cards = [
        {
            "element_id": f"profile:{i}",
            "heading": p.get("heading", ""),
            "body": p.get("body", ""),
            "query_refs": p.get("query_refs", []),
            "chart": None if p.get("chart") == main_chart else p.get("chart"),
        }
        for i, p in enumerate(profiles or [])
    ]
    return {
        "element_id": "report",
        "summary": summary,
        "headlines": headline_tiles,
        "insights": insight_cards,
        "profiles": profile_cards,
        "main_chart": main_chart,
    }
