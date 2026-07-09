"""Tests for the report helpers that remain after the sandbox restructure.

The draft/assembly layer (HeadlineDraft/assemble_report) is gone — the sandbox
agent builds the report dict directly via the ``build_report`` skill (covered
by services/data-agent/tests/test_skills.py). What lives here is the surviving
architecture-independent surface: the structural lint the eval suite uses (K5)
and the legacy primary-query picker.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "data-agent"))

from agent.report import report_structural_issues, select_primary_query  # noqa: E402


def _good_report() -> dict:
    return {
        "element_id": "report",
        "summary": "Normanhurst has out-grown Hornsby.",
        "headlines": [
            {
                "element_id": "headline:0",
                "label": "Hornsby latest",
                "value": "$1.6M",
                "basis": "6-mo rolling",
                "related": False,
                "query_ref": "Q1",
            }
        ],
        "insights": [
            {
                "element_id": "insight:0",
                "heading": "Gap widening",
                "body": "Because...",
                "query_refs": ["Q1"],
                "chart": None,
            }
        ],
        "profiles": [],
        "main_chart": {"mark": "line", "encoding": {}, "data": {"values": []}},
        "queries": [
            {
                "element_id": "query:Q1",
                "ref": "Q1",
                "purpose": "series",
                "sql": "select 1",
                "columns": ["suburb"],
                "rows": [["HORNSBY"]],
                "row_count": 1,
            }
        ],
        "knowledge_pages_used": ["trend-charts"],
        "knowledge_version": "abc123",
    }


def test_good_report_has_no_structural_issues() -> None:
    assert report_structural_issues(_good_report()) == []


def test_dangling_query_ref_is_flagged() -> None:
    report = _good_report()
    report["insights"][0]["query_refs"] = ["Q9"]  # does not exist
    issues = report_structural_issues(report)
    assert any("unknown query Q9" in i for i in issues)


def test_empty_summary_is_flagged() -> None:
    report = _good_report()
    report["summary"] = "  "
    assert any("summary is empty" in i for i in report_structural_issues(report))


def test_headline_without_value_is_flagged() -> None:
    report = _good_report()
    report["headlines"][0]["value"] = ""
    assert any("has no value" in i for i in report_structural_issues(report))


def test_missing_knowledge_version_is_flagged() -> None:
    report = _good_report()
    report["knowledge_version"] = ""
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
