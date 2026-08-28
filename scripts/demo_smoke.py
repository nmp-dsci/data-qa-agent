#!/usr/bin/env python3
"""End-to-end smoke test for DEMO MODE (s38): the walk-in door, replayed chat,
the local governed SQL executor, gated LLM endpoints, exhibit reads, guardrails.

Run against a stack started with DEMO_MODE=1:
    DEMO_MODE=1 docker compose up -d backend-api   (or make demo-up)
    python scripts/demo_smoke.py

Uses only the stdlib. API base follows the same env vars as smoke_test.py.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = os.environ.get(
    "SMOKE_API_URL", f"http://localhost:{os.environ.get('API_HOST_PORT', '8000')}"
)
TIMEOUT = int(os.environ.get("SMOKE_TIMEOUT_S", "60"))

_passed = 0
_failed = 0


def check(name: str, ok: bool, note: str = "") -> None:
    global _passed, _failed
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name} {note}")
    if ok:
        _passed += 1
    else:
        _failed += 1


def req(method: str, path: str, body: dict | None = None, token: str | None = None):
    """Return (status, parsed-json-or-None). HTTP errors come back as status."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "X-Client-Channel": "web"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=TIMEOUT) as resp:
            return resp.status, json.loads(resp.read() or "null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or "null")
        except Exception:  # noqa: BLE001
            return e.code, None


def main() -> None:
    print(f"demo smoke against {API}")

    print("1. the demo door")
    status, cfg = req("GET", "/auth/config")
    check("auth config reports demo", status == 200 and cfg.get("auth_mode") == "demo")
    status, login = req("POST", "/auth/demo-login")
    check("demo-login mints a session", status == 200 and bool(login.get("access_token")))
    if status != 200:
        sys.exit("cannot continue without a session — is DEMO_MODE=1 on the backend?")
    tok = login["access_token"]
    check("session is the demo user", login["user"]["username"] == "demo")

    print("2. the chip rail")
    status, chips = req("GET", "/demo/questions")
    check("questions endpoint serves the pack", status == 200 and len(chips or []) >= 5,
          f"(count={len(chips or [])})")
    chip_q = chips[0]["question"]

    print("3. replayed chat")
    status, ans = req("POST", "/ask", {"question": chip_q}, tok)
    check("exact chip question answers", status == 200 and ans.get("engine") == "demo_replay",
          f"(engine={ans.get('engine') if ans else None})")
    check("replay carries the recorded report", bool(ans.get("report")))
    check("replay carries the recorded SQL", bool(ans.get("sql")))
    check("exact match has no closest-answer note", ans.get("demo_matched_question") in (None, ""))

    fuzzy = chip_q.replace("Which", "What").replace("?", "").lower()
    status, ans2 = req("POST", "/ask", {"question": fuzzy}, tok)
    check("fuzzy variant matches a recording", status == 200 and bool(ans2.get("sql")))

    status, miss = req("POST", "/ask", {"question": "write me a poem about kubernetes"}, tok)
    check("unrelated question misses honestly",
          status == 200 and "recorded" in (miss.get("answer") or ""))

    print("4. live SQL through the local governed executor")
    status, rows = req("POST", "/sql",
                       {"sql": "SELECT * FROM marts.property_rent LIMIT 5"}, tok)
    ok_rows = (
        status == 200 and not (rows or {}).get("error") and (rows or {}).get("row_count", 0) > 0
    )
    check("governed SELECT returns rows", ok_rows,
          f"(rows={(rows or {}).get('row_count')}, err={(rows or {}).get('error')})")
    status, denied = req("POST", "/sql", {"sql": "DELETE FROM app.users"}, tok)
    check("write SQL is refused", status == 200 and bool((denied or {}).get("error")))
    status, cat = req("GET", "/schema/catalog", token=tok)
    tables = (cat or {}).get("tables") or []
    check("schema catalog served locally", status == 200 and len(tables) > 0,
          f"(tables={len(tables)})")
    check("visitor catalog stays analytical (marts/staging only)",
          all(t.get("schema") in ("marts", "staging") for t in tables))

    print("5. LLM endpoints are closed")
    status, _ = req("POST", "/sql/ai", {"action": "generate", "prompt": "top suburbs"}, tok)
    check("SQL AI assist 501s", status == 501)

    print("6. exhibits are readable, mutations are not")
    status, goldens = req("GET", "/admin/eval-goldens", token=tok)
    check("goldens gallery readable by visitor", status == 200 and len(goldens or []) > 0,
          f"(count={len(goldens or [])})")
    status, _ = req("GET", "/admin/events?limit=5", token=tok)
    check("admin events readable (static exhibit)", status == 200)
    status, _ = req("POST", "/admin/eval-goldens",
                    {"question": "x", "dataset": "nsw_rent"}, tok)
    check("golden create still 403s", status == 403, f"(status={status})")
    status, _ = req("GET", "/analytics/summary", token=tok)
    check("analytics is owner-only (403 for visitor)", status == 403, f"(status={status})")

    print("7. analytics beacon")
    status, _ = req("POST", "/events",
                    {"event_type": "demo_landing_view", "session_id": "smoke",
                     "payload": {"visitor_id": "smoke-visitor"}})
    check("anonymous event accepted", status == 201)

    print("8. per-IP rate limit")
    last = 200
    for _ in range(15):
        last, _ = req("POST", "/ask", {"question": chip_q}, tok)
        if last == 429:
            break
    check("ask flood trips 429 demo_rate_limited", last == 429, f"(last={last})")

    print()
    if _failed:
        sys.exit(f"DEMO SMOKE FAILED ✗  ({_passed} passed, {_failed} failed)")
    print(f"DEMO SMOKE PASSED ✓  ({_passed} checks)")


if __name__ == "__main__":
    main()
