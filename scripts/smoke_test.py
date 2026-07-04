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


def _post(
    path: str, body: dict, token: str | None = None, channel: str | None = None
) -> dict:
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # Identify the client channel the way the web app does; omitted -> backend
    # records the run as 'api' (a direct API hit). Lets the audit assert both.
    if channel:
        headers["X-Client-Channel"] = channel
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


def ask(token: str, question: str, channel: str | None = None) -> dict:
    return _post("/ask", {"question": question}, token=token, channel=channel)


def run_sql(token: str, sql: str, channel: str | None = None) -> dict:
    return _post("/sql", {"sql": sql}, token=token, channel=channel)


def _post_status(path: str, body: dict, token: str) -> int:
    """POST returning the HTTP status code (for expected-4xx guardrail checks)."""
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    req = urllib.request.Request(API + path, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def _is_select_shaped(sql: str) -> bool:
    """SELECT, or a CTE (WITH ...) — both are guardrail-approved read-only shapes.
    Growth/yield questions now require a CTE (no precomputed growth%/yield%
    column), so a plain SELECT is no longer the only valid shape."""
    s = sql.lower().lstrip()
    return s.startswith("select") or s.startswith("with")


def _no_real_data(result: dict) -> bool:
    """True if RLS isolation held: either zero rows, or every returned value is
    NULL/zero aggregate counts. The agent can retry a zero-rows result with a
    follow-up query (e.g. checking min/max month) that itself returns an
    aggregate row without leaking any real sales/rent figures — that's still
    isolation holding, just not a literal zero row_count."""
    if result["row_count"] == 0:
        return True
    return all(v is None or v == 0 for row in result["rows"] for v in row)


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal failures
        status = "PASS" if cond else "FAIL"
        if not cond:
            failures += 1
        print(f"  [{status}] {name} {extra}")

    growth_q = "What are the top growth suburbs for sale price and rent?"

    print("1. user1 (has property data access) asks for top growth suburbs [channel=web]")
    t1 = login("user1")
    # Simulate the web app: send X-Client-Channel: web so this run is attributed
    # to the 'web' channel. Admin/editor calls below stay unset -> 'api'.
    r1 = ask(t1, growth_q, channel="web")
    print(f"     answer: {r1['answer'][:90]}")
    check("user1 gets rows back", r1["row_count"] > 0, f"(row_count={r1['row_count']})")
    check("user1 answer is non-empty", bool(r1["answer"].strip()))
    check("sql is a SELECT or CTE", _is_select_shaped(r1.get("sql") or ""))
    check(
        "answer combines sales + rent growth",
        {"sales_growth_pct", "rent_growth_pct"} <= set(r1.get("columns", [])),
    )

    print("2. user2 (NO access) asks the same question -> RLS should hide rows")
    t2 = login("user2")
    r2 = ask(t2, growth_q)
    print(f"     answer: {r2['answer'][:90]}")
    check(
        "user2 sees zero rows or only NULLs (isolation)",
        _no_real_data(r2),
        f"(row_count={r2['row_count']})",
    )

    print("3. admin asks a count question -> sees data")
    ta = login("admin")
    ra = ask(ta, "How many suburbs do we have?")
    print(f"     answer: {ra['answer'][:90]}")
    check("admin gets a count", ra["row_count"] > 0)

    print("4. user1 asks for rental yield -> combines sales + rent (both datasets) [channel=web]")
    ry = ask(t1, "What are the best suburbs for rental yield?", channel="web")
    print(f"     answer: {ry['answer'][:90]}")
    check("user1 gets yield rows back", ry["row_count"] > 0, f"(row_count={ry['row_count']})")
    check("yield sql is a SELECT or CTE", _is_select_shaped(ry.get("sql") or ""))
    check("answer includes gross_yield_pct", "gross_yield_pct" in ry.get("columns", []))

    print("5. admin audit view includes query runs")
    query_runs = _get("/admin/query-runs", ta)
    check("query run audit has entries", len(query_runs) >= 4, f"(count={len(query_runs)})")
    check(
        "latest audit row includes SQL",
        bool(query_runs and query_runs[0].get("sql_text")),
    )

    editor_sql = (
        "SELECT suburb, count(*) AS n FROM marts.mart_sales_summary "
        "WHERE property_type = 'ALL' GROUP BY suburb ORDER BY n DESC LIMIT 5"
    )

    print("6. SQL editor: user1 runs SQL directly -> governed rows")
    e1 = run_sql(t1, editor_sql)
    check("editor returns rows for user1", e1["row_count"] > 0, f"(row_count={e1['row_count']})")
    check("editor result has no error", not e1.get("error"), f"({e1.get('error')})")
    check("editor engine is sql_editor", e1.get("engine") == "sql_editor")

    print("7. SQL editor: user2 runs the SAME SQL -> RLS isolates (0 rows)")
    e2 = run_sql(t2, editor_sql)
    check("editor isolates user2", _no_real_data(e2), f"(row_count={e2['row_count']})")

    print("8. SQL editor guardrail rejects a write/DDL statement")
    # A CTE-hidden DELETE — the AST guardrail must reject it (400 from the agent
    # surfaces as an error field, not rows). Returned as a structured error.
    bad = run_sql(t1, "WITH x AS (DELETE FROM app.users RETURNING id) SELECT * FROM x")
    check("guardrail flags unsafe SQL", bool(bad.get("error")), f"({bad.get('error')})")

    print("9. SQL editor history + AI generate")
    hist = _get("/sql/history", t1)
    check("user1 history has editor runs", len(hist) >= 1, f"(count={len(hist)})")
    gen = _post(
        "/sql/ai", {"action": "generate", "prompt": "top suburbs by rent growth"}, token=t1
    )
    check(
        "AI generate returns SQL",
        _is_select_shaped(gen.get("sql") or ""),
        f"(engine={gen.get('engine')})",
    )

    print("10. audit distinguishes sql_editor runs by source")
    runs2 = _get("/admin/query-runs", ta)
    check(
        "audit has a sql_editor-source run",
        any(r.get("source") == "sql_editor" for r in runs2),
    )

    print("11. audit captures the entry-point channel (web app vs direct API hit)")
    channels = {r.get("channel") for r in runs2}
    # user1's asks were tagged web; admin/editor calls were untagged -> api.
    check("audit has a web-channel run", "web" in channels, f"(channels={sorted(channels)})")
    check("audit has an api-channel run", "api" in channels, f"(channels={sorted(channels)})")

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
