"""Tests for InsightReport assembly + structural checks (K2/K5)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "data-agent"))

from agent.report import (  # noqa: E402
    HeadlineDraft,
    InsightDraft,
    ReportDraft,
    assemble_report,
    report_structural_issues,
    select_primary_query,
)

_QUERIES = {
    "Q1": {
        "sql": "select 1",
        "columns": ["suburb"],
        "rows": [["HORNSBY"]],
        "row_count": 1,
        "purpose": "series",
    },
}
_CHARTS = {"C1": {"mark": "line", "encoding": {}, "data": {"values": []}}}


def _good_draft() -> ReportDraft:
    return ReportDraft(
        summary="Normanhurst has out-grown Hornsby.",
        headlines=[HeadlineDraft(label="Hornsby latest", value="$1.6M", query_ref="Q1")],
        insights=[InsightDraft(heading="Gap widening", body="Because...", query_refs=["Q1"])],
        main_chart_ref="C1",
    )


def test_assemble_resolves_refs_and_assigns_ids() -> None:
    report = assemble_report(
        _good_draft(),
        queries=_QUERIES,
        charts=_CHARTS,
        knowledge_pages=["trend-charts"],
        knowledge_version="abc123",
    )
    assert report["insights"][0]["element_id"] == "insight:0"
    assert report["main_chart"] == _CHARTS["C1"]
    assert report["queries"][0]["ref"] == "Q1"
    assert report["knowledge_version"] == "abc123"


def test_good_report_has_no_structural_issues() -> None:
    report = assemble_report(
        _good_draft(),
        queries=_QUERIES,
        charts=_CHARTS,
        knowledge_pages=["trend-charts"],
        knowledge_version="abc123",
    )
    assert report_structural_issues(report) == []


def test_dangling_query_ref_is_flagged() -> None:
    draft = _good_draft()
    draft.insights[0].query_refs = ["Q9"]  # does not exist
    report = assemble_report(
        draft,
        queries=_QUERIES,
        charts=_CHARTS,
        knowledge_pages=[],
        knowledge_version="abc123",
    )
    issues = report_structural_issues(report)
    assert any("unknown query Q9" in i for i in issues)


def test_empty_summary_is_flagged() -> None:
    draft = _good_draft()
    draft.summary = "  "
    report = assemble_report(
        draft,
        queries=_QUERIES,
        charts=_CHARTS,
        knowledge_pages=[],
        knowledge_version="abc123",
    )
    assert any("summary is empty" in i for i in report_structural_issues(report))


def test_missing_knowledge_version_is_flagged() -> None:
    report = assemble_report(
        _good_draft(),
        queries=_QUERIES,
        charts=_CHARTS,
        knowledge_pages=[],
        knowledge_version="",
    )
    assert any("knowledge_version" in i for i in report_structural_issues(report))


def test_primary_query_prefers_domain_data_over_catalog_probe() -> None:
    primary = select_primary_query(
        {
            "Q1": {
                "sql": "select column_name from information_schema.columns",
                "row_count": 7,
            },
            "Q2": {
                "sql": "select * from marts.property_sales where false",
                "row_count": 0,
            },
        }
    )
    assert primary is not None
    assert primary["sql"].startswith("select * from marts.")
