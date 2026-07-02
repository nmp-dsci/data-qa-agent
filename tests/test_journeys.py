"""Journey evals — runs evals/journeys.yaml against a live stack.

These are end-to-end: they exercise the real /auth, /me and /ask flow (login ->
ask -> agent answers) and double as the RLS isolation check. They need a running
stack (`make up`); when the API is unreachable the whole module is skipped, so
`uv run pytest` stays green without Docker. Point elsewhere with DATAQA_API_URL.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
import yaml

API = os.environ.get("DATAQA_API_URL", "http://localhost:8000")
JOURNEYS = Path(__file__).resolve().parents[1] / "evals" / "journeys.yaml"


def _request(method: str, path: str, body: dict | None, token: str | None) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, None


def _stack_up() -> bool:
    try:
        status, _ = _request("GET", "/health", None, None)
        return status == 200
    except urllib.error.URLError:
        return False


pytestmark = pytest.mark.skipif(
    not _stack_up(), reason=f"stack not reachable at {API} (run `make up`)"
)


def _load_journeys() -> list[dict]:
    return yaml.safe_load(JOURNEYS.read_text())["journeys"]


def _ids(journeys: list[dict]) -> list[str]:
    return [j["name"] for j in journeys]


_JOURNEYS = _load_journeys()


@pytest.mark.parametrize("journey", _JOURNEYS, ids=_ids(_JOURNEYS))
def test_journey(journey: dict) -> None:
    expectations = journey["expect"]
    anonymous = journey.get("anonymous", False)
    question = journey.get("question")

    # Sign in (unless anonymous), tolerating an expected rejection.
    token: str | None = None
    login_status = 0
    if not anonymous:
        login_status, login_body = _request(
            "POST", "/auth/dev-login", {"username": journey["as_user"]}, None
        )
        if login_status == 200:
            token = login_body["access_token"]

    # Ask once if any expectation needs the agent.
    ask_result: dict | None = None
    if question and token:
        ask_status, ask_result = _request("POST", "/ask", {"question": question}, token)
        assert ask_status == 200, f"/ask failed with {ask_status}"

    for exp in expectations:
        _check(exp, token=token, login_status=login_status, ask_result=ask_result)


def _check(exp: Any, *, token: str | None, login_status: int, ask_result: dict | None) -> None:
    if isinstance(exp, str):
        if exp == "login_succeeds":
            assert login_status == 200, f"expected login to succeed, got {login_status}"
        elif exp == "login_rejected":
            assert login_status >= 400, f"expected login to be rejected, got {login_status}"
        elif exp == "unauthenticated_rejected":
            status, _ = _request("GET", "/me", None, None)
            assert status == 401, f"expected 401 for anonymous /me, got {status}"
        elif exp == "agent_answers_successfully":
            assert ask_result is not None
            assert ask_result["answer"].strip(), "expected a non-empty answer"
            assert ask_result["row_count"] > 0, "expected rows in the answer"
        elif exp == "sql_is_select_only":
            assert ask_result is not None
            sql = (ask_result.get("sql") or "").lower().lstrip()
            # Growth/yield questions require a CTE now (no precomputed
            # growth%/yield% column) — WITH is as guardrail-approved as SELECT.
            assert sql.startswith("select") or sql.startswith("with"), (
                f"expected a SELECT or CTE, got: {sql[:40]!r}"
            )
        elif exp == "no_rows_or_access_denied":
            assert ask_result is not None
            # Zero rows is the common case, but a retry-driven follow-up query
            # (e.g. checking min/max month after a zero-rows result) can return
            # a row of NULLs without leaking any real data — isolation still
            # held, just not as a literal zero row_count.
            rows = ask_result.get("rows") or []
            no_real_data = ask_result["row_count"] == 0 or all(
                v is None for row in rows for v in row
            )
            assert no_real_data, f"expected no real data (RLS isolation), got rows: {rows}"
        elif exp == "chart_present":
            assert ask_result is not None
            assert isinstance(ask_result.get("chart"), dict), "expected a chart in the response"
        else:
            raise AssertionError(f"unknown expectation: {exp!r}")
        return

    # dict-form expectations, e.g. {me_role: admin} or {mentions: [suburb]}
    ((key, value),) = exp.items()
    if key == "me_role":
        status, me = _request("GET", "/me", None, token)
        assert status == 200, f"/me failed with {status}"
        assert me["role"] == value, f"expected role {value!r}, got {me['role']!r}"
    elif key == "mentions":
        assert ask_result is not None
        haystack = (ask_result["answer"] + " " + " ".join(ask_result["columns"])).lower()
        for term in value:
            assert term.lower() in haystack, f"expected mention of {term!r}"
    elif key == "chart_mark":
        assert ask_result is not None
        chart = ask_result.get("chart") or {}
        mark = chart.get("mark")
        mark_type = mark.get("type") if isinstance(mark, dict) else mark
        assert mark_type == value, f"expected chart mark {value!r}, got {mark_type!r}"
    elif key == "chart_encoding_channels":
        assert ask_result is not None
        chart = ask_result.get("chart") or {}
        encoding = chart.get("encoding") or {}
        missing = [c for c in value if c not in encoding]
        assert not missing, f"chart encoding missing channels: {missing}"
    else:
        raise AssertionError(f"unknown expectation: {exp!r}")
