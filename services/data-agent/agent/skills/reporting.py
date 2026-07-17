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
def build_insights(
    *,
    insights: list[dict[str, Any]],
    profiles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Pass-2 patch: insight cards (and optional profiles) that EXPLAIN the
    Page-1 numbers — slice the same frame by its attribute columns
    (driver_analysis + comparison_chart), never re-extract.

    Assign to ``result`` in the second run_analysis pass; it merges into the
    report already built by ``build_report`` (pass 1) — it never replaces it.
    """
    insight_cards = [
        {
            "element_id": f"insight:{i}",
            "heading": ins.get("heading", ""),
            "body": ins.get("body", ""),
            "query_refs": ins.get("query_refs", []),
            "chart": ins.get("chart"),
        }
        for i, ins in enumerate(insights or [])
    ]
    profile_cards = [
        {
            "element_id": f"profile:{i}",
            "heading": p.get("heading", ""),
            "body": p.get("body", ""),
            "query_refs": p.get("query_refs", []),
            "chart": p.get("chart"),
        }
        for i, p in enumerate(profiles or [])
    ]
    return {
        "element_id": "insights_patch",
        "insights": insight_cards,
        "profiles": profile_cards,
    }


@skill
def data_table(
    df: Any,
    *,
    columns: list[dict[str, Any]],
    title: str | None = None,
    variant: str = "plain",
    bar_key: str | None = None,
) -> dict[str, Any]:
    """A table payload from a frame: the DataTable wire shape (s20).

    ``columns`` is ``[{"key","label","align"?,"tone"?,"format"?}, ...]``;
    ``variant`` is plain | comparison | ranked (ranked draws an inline bar sized
    by ``bar_key``). Pass the result to ``build_report(table=...)`` so the
    object builder lifts it into a ``table`` page object.
    """
    return {
        "title": title,
        "variant": variant,
        "columns": list(columns),
        "rows": df.to_dict("records"),
        "bar_key": bar_key,
    }


@skill
def build_report(
    *,
    summary: str,
    headlines: list[dict[str, Any]] | None = None,
    insights: list[dict[str, Any]] | None = None,
    profiles: list[dict[str, Any]] | None = None,
    main_chart: dict[str, Any] | None = None,
    table: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the narrative into the app's report shape (minus queries/version).

    Produces the same element-shape the server assembles today: a one-sentence
    ``summary``, headline tiles, 2-4 insight cards, an optional profile section,
    the primary ``main_chart``, and (s20) an optional ``table`` payload from
    ``data_table`` — the object builder lifts it into a ``table`` page object.
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
        "table": table,
    }
