"""The structured InsightReport contract (K2).

``ReportDraft`` is the agent's Pydantic AI ``output_type`` — the narrative the
model authors. It references queries and charts by id (Q1, C1) instead of
embedding rows, so the model can never fabricate data: the server resolves those
ids from what actually ran and assembles the final ``report`` dict the frontend
renders. Every element gets a stable ``element_id`` (the feedback anchor, §06)
and the report records the knowledge version that produced it (staleness, §06).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HeadlineDraft(BaseModel):
    label: str = Field(description="e.g. 'Normanhurst · 5yr growth'")
    value: str = Field(description="formatted value, e.g. '+19.2%' or '$2.18M'")
    basis: str = Field(default="", description="how it was derived, e.g. '6-mo rolling, Mar 2026'")
    related: bool = Field(
        default=False, description="True if a context metric not directly asked for"
    )
    query_ref: str | None = Field(default=None, description="e.g. 'Q1'")


class InsightDraft(BaseModel):
    heading: str = Field(description="the finding stated as a claim")
    body: str = Field(description="1-3 sentences on why it matters")
    query_refs: list[str] = Field(default_factory=list)
    chart_ref: str | None = Field(default=None, description="optional inline chart id, e.g. 'C2'")


class ProfileDraft(BaseModel):
    heading: str
    body: str
    query_refs: list[str] = Field(default_factory=list)
    chart_ref: str | None = None


class ReportDraft(BaseModel):
    """What the agent outputs. Data is referenced by id, never embedded."""

    summary: str = Field(description="one sentence directly answering the question")
    headlines: list[HeadlineDraft] = Field(default_factory=list)
    insights: list[InsightDraft] = Field(default_factory=list)
    profiles: list[ProfileDraft] = Field(default_factory=list)
    main_chart_ref: str | None = Field(
        default=None, description="the report's primary chart id, e.g. 'C1'"
    )


def assemble_report(
    draft: ReportDraft,
    *,
    queries: dict[str, dict[str, Any]],
    charts: dict[str, dict[str, Any]],
    knowledge_pages: list[str],
    knowledge_version: str,
) -> dict[str, Any]:
    """Resolve a draft's id references into the final report dict for the API."""

    def chart_for(ref: str | None) -> dict[str, Any] | None:
        return charts.get(ref) if ref else None

    def inline_chart_for(ref: str | None) -> dict[str, Any] | None:
        # The report's primary chart is rendered once, at the top. If an insight
        # or profile also cites that same chart ref, the frontend would render
        # the identical chart a second time inside the card (the "trend chart
        # appears twice" report). An inline card chart must be a DIFFERENT chart
        # (e.g. a comparison bar), so drop a ref that just repeats the main one.
        if ref and ref == draft.main_chart_ref:
            return None
        return chart_for(ref)

    headlines = [
        {
            "element_id": f"headline:{i}",
            "label": h.label,
            "value": h.value,
            "basis": h.basis,
            "related": h.related,
            "query_ref": h.query_ref,
        }
        for i, h in enumerate(draft.headlines)
    ]
    insights = [
        {
            "element_id": f"insight:{i}",
            "heading": ins.heading,
            "body": ins.body,
            "query_refs": ins.query_refs,
            "chart": inline_chart_for(ins.chart_ref),
        }
        for i, ins in enumerate(draft.insights)
    ]
    profiles = [
        {
            "element_id": f"profile:{i}",
            "heading": p.heading,
            "body": p.body,
            "query_refs": p.query_refs,
            "chart": inline_chart_for(p.chart_ref),
        }
        for i, p in enumerate(draft.profiles)
    ]
    query_list = [
        {
            "element_id": f"query:{ref}",
            "ref": ref,
            "purpose": q.get("purpose", ""),
            "sql": q.get("sql"),
            "columns": q.get("columns", []),
            "rows": q.get("rows", []),
            "row_count": q.get("row_count", 0),
        }
        for ref, q in queries.items()
    ]
    return {
        "element_id": "report",
        "summary": draft.summary,
        "headlines": headlines,
        "insights": insights,
        "profiles": profiles,
        "main_chart": chart_for(draft.main_chart_ref),
        "queries": query_list,
        "knowledge_pages_used": knowledge_pages,
        "knowledge_version": knowledge_version,
    }


def report_structural_issues(report: dict[str, Any]) -> list[str]:
    """Deterministic structural checks used by the eval suite (K5).

    These catch a report regressing to an empty or dangling shape without any
    LLM judgement: a non-empty summary, every cited query_ref actually present,
    insights that carry a claim + evidence, and a recorded knowledge version.
    """
    issues: list[str] = []
    if not (report.get("summary") or "").strip():
        issues.append("summary is empty")

    query_refs = {q["ref"] for q in report.get("queries", [])}
    if not query_refs:
        issues.append("no queries were recorded")

    for h in report.get("headlines", []):
        if not h.get("value"):
            issues.append(f"headline {h.get('element_id')} has no value")
        ref = h.get("query_ref")
        if ref and ref not in query_refs:
            issues.append(f"headline {h.get('element_id')} cites unknown query {ref}")

    for ins in report.get("insights", []):
        if not (ins.get("heading") or "").strip() or not (ins.get("body") or "").strip():
            issues.append(f"insight {ins.get('element_id')} missing heading/body")
        for ref in ins.get("query_refs", []):
            if ref not in query_refs:
                issues.append(f"insight {ins.get('element_id')} cites unknown query {ref}")

    for p in report.get("profiles", []):
        for ref in p.get("query_refs", []):
            if ref not in query_refs:
                issues.append(f"profile {p.get('element_id')} cites unknown query {ref}")

    if not report.get("knowledge_version"):
        issues.append("report has no knowledge_version")

    return issues


def select_primary_query(queries: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the query used for legacy flat API fields.

    The structured report keeps every successful SQL call. Legacy fields
    (`sql`, `rows`, `row_count`) should reflect governed domain data rather than
    catalog probes such as `information_schema` queries the model may run while
    planning.
    """
    if not queries:
        return None
    domain_queries = [q for q in queries.values() if _is_domain_query(q.get("sql", ""))]
    pool = domain_queries or list(queries.values())
    return max(pool, key=lambda q: q.get("row_count", 0))


def _is_domain_query(sql: str) -> bool:
    lower = sql.lower()
    return any(schema in lower for schema in ("marts.", "staging.", "raw."))
