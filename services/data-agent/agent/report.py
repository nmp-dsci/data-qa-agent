"""Report helpers — structural checks and primary-query selection.

The sandbox agent builds the final report dict directly (via the ``build_report``
skill), so no ``output_type`` draft/assembly layer is needed here anymore. What
remains are architecture-independent helpers: a deterministic structural lint used
by the eval suite (K5), and the picker for the legacy flat API fields.
"""

from __future__ import annotations

from typing import Any


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
