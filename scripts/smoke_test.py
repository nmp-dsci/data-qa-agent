#!/usr/bin/env python3
"""End-to-end smoke test: login -> ask -> response, plus RLS isolation.

Run against a running stack (`make up`):  python scripts/smoke_test.py
Uses only the stdlib so it needs no dependencies.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

API = "http://localhost:8000"


def _post(path: str, body: dict, token: str | None = None) -> dict:
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(API + path, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _get(path: str, token: str) -> list[dict]:
    req = urllib.request.Request(
        API + path, headers={"Authorization": f"Bearer {token}"}, method="GET"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def login(username: str) -> str:
    return _post("/auth/dev-login", {"username": username})["access_token"]


def ask(token: str, question: str) -> dict:
    return _post("/ask", {"question": question}, token=token)


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal failures
        status = "PASS" if cond else "FAIL"
        if not cond:
            failures += 1
        print(f"  [{status}] {name} {extra}")

    growth_q = "What are the top growth suburbs for sale price and rent?"

    print("1. user1 (has property data access) asks for top growth suburbs")
    t1 = login("user1")
    r1 = ask(t1, growth_q)
    print(f"     answer: {r1['answer'][:90]}")
    check("user1 gets rows back", r1["row_count"] > 0, f"(row_count={r1['row_count']})")
    check("user1 answer is non-empty", bool(r1["answer"].strip()))
    check("sql is a SELECT", (r1.get("sql") or "").lower().lstrip().startswith("select"))
    check(
        "answer combines sales + rent growth",
        {"sales_growth_pct", "rent_growth_pct"} <= set(r1.get("columns", [])),
    )

    print("2. user2 (NO access) asks the same question -> RLS should hide rows")
    t2 = login("user2")
    r2 = ask(t2, growth_q)
    print(f"     answer: {r2['answer'][:90]}")
    check(
        "user2 sees zero rows (isolation)", r2["row_count"] == 0, f"(row_count={r2['row_count']})"
    )

    print("3. admin asks a count question -> sees data")
    ta = login("admin")
    ra = ask(ta, "How many suburbs do we have?")
    print(f"     answer: {ra['answer'][:90]}")
    check("admin gets a count", ra["row_count"] > 0)

    print("4. admin audit view includes query runs")
    query_runs = _get("/admin/query-runs", ta)
    check("query run audit has entries", len(query_runs) >= 3, f"(count={len(query_runs)})")
    check(
        "latest audit row includes SQL",
        bool(query_runs and query_runs[0].get("sql_text")),
    )

    print()
    if failures:
        print(f"SMOKE TEST FAILED: {failures} check(s) failed")
        return 1
    print("SMOKE TEST PASSED ✓  (login -> ask -> response works; RLS isolates user2)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.URLError as exc:
        print(f"Could not reach {API} — is the stack up? ({exc})")
        sys.exit(2)
