"""Report-structure evals (K5) — runs evals/report_evals.yaml against a live stack.

End-to-end: exercises the real /ask flow and asserts each answer is a well-formed
InsightReport (deterministic structural + light grounding checks; LLM-judge
deferred). Needs a running stack (`make up`) AND a configured LLM provider — the
offline stub returns no report, so cases are skipped when no report comes back.
Point elsewhere with DATAQA_API_URL.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "data-agent"))

from agent.report import report_structural_issues  # noqa: E402

API = os.environ.get("DATAQA_API_URL", "http://localhost:8000")
CASES = ROOT / "evals" / "report_evals.yaml"


def _request(method: str, path: str, body: dict | None, token: str | None) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, None


def _stack_up() -> bool:
    try:
        status, _ = _request("GET", "/health", None, None)
        return status == 200
    except urllib.error.URLError:
        return False


pytestmark = pytest.mark.skipif(not _stack_up(), reason=f"stack not reachable at {API}")


def _load() -> list[dict]:
    return yaml.safe_load(CASES.read_text())["cases"]


_CASES = _load()


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_report_case(case: dict) -> None:
    status, login = _request("POST", "/auth/dev-login", {"username": case["as_user"]}, None)
    assert status == 200, f"login failed ({status})"
    token = login["access_token"]

    status, result = _request("POST", "/ask", {"question": case["question"]}, token)
    assert status == 200, f"/ask failed ({status})"

    report = result.get("report")
    if report is None:
        pytest.skip("no report (offline stub / no LLM provider configured)")

    for exp in case["expect"]:
        _check(exp, report, result)


def _check(exp: Any, report: dict, result: dict) -> None:
    if isinstance(exp, str):
        if exp == "report_present":
            assert report is not None
        elif exp == "report_well_formed":
            issues = report_structural_issues(report)
            assert not issues, f"structural issues: {issues}"
        elif exp == "has_headline":
            assert report.get("headlines"), "expected at least one headline"
        elif exp == "has_insight":
            assert report.get("insights"), "expected at least one insight"
        elif exp == "trend_chart":
            assert report.get("main_chart"), "expected a main chart"
        else:
            raise AssertionError(f"unknown expectation: {exp!r}")
        return
    ((key, value),) = exp.items()
    if key == "mentions":
        cols = " ".join(c for q in report.get("queries", []) for c in q.get("columns", []))
        haystack = (report.get("summary", "") + " " + cols).lower()
        for term in value:
            assert term.lower() in haystack, f"expected mention of {term!r}"
    else:
        raise AssertionError(f"unknown expectation: {exp!r}")
