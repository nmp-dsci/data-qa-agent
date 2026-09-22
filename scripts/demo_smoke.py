#!/usr/bin/env python3
"""End-to-end smoke test for DEMO MODE (s38): the walk-in door, replayed chat,
the local governed SQL executor, gated LLM endpoints, exhibit reads, guardrails.

Run against a stack started with DEMO_MODE=1:
    DEMO_MODE=1 docker compose up -d backend-api   (or make demo-up)
    python scripts/demo_smoke.py

s52 — the DB-less variant. With DB_DISABLED=1 in the environment the checks
match the DB-less demo contract instead (services/backend-api/tests/
test_db_disabled.py): no SQL, no admin reads (404, not 403), /health/db
"disabled", empty conversations, events accepted, and the exhibit dump served
by the FRONTEND origin (FRONTEND_URL, default http://localhost:5230):
    make demo-dbless-up && make demo-dbless-smoke

Uses only the stdlib. API base follows the same env vars as smoke_test.py.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = os.environ.get("SMOKE_API_URL", f"http://localhost:{os.environ.get('API_HOST_PORT', '8000')}")
FRONTEND = os.environ.get("FRONTEND_URL", "http://localhost:5230").rstrip("/")
TIMEOUT = int(os.environ.get("SMOKE_TIMEOUT_S", "60"))
DB_DISABLED = os.environ.get("DB_DISABLED", "0") not in ("", "0", "false", "no")
# The list-length the Evaluations tab asks for (EvalsPage -> getEvalRuns()).
EVAL_RUNS_LIMIT = int(os.environ.get("SMOKE_EVAL_RUNS_LIMIT", "50"))

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


def fetch_static(url: str):
    """Return (status, content-type, parsed-json-or-None) for a frontend-origin GET."""
    r = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=TIMEOUT) as resp:
            ctype = resp.headers.get("content-type", "")
            raw = resp.read()
            try:
                return resp.status, ctype, json.loads(raw)
            except Exception:  # noqa: BLE001
                return resp.status, ctype, None
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("content-type", ""), None
    except urllib.error.URLError as e:
        return 0, str(e.reason), None


def main_db_disabled() -> None:
    print(f"demo smoke (DB-less, s52) against {API}; exhibits from {FRONTEND}")

    print("1. the demo door, with no database behind it")
    status, cfg = req("GET", "/auth/config")
    check("auth config reports demo", status == 200 and cfg.get("auth_mode") == "demo")
    status, health = req("GET", "/health/db")
    check(
        "/health/db reports disabled (no wake, no spinner)",
        status == 200 and (health or {}).get("status") == "disabled",
        f"(status={(health or {}).get('status')})",
    )
    status, login = req("POST", "/auth/demo-login")
    check("demo-login mints a session", status == 200 and bool(login.get("access_token")))
    if status != 200:
        sys.exit("cannot continue without a session — is DEMO_MODE=1 DB_DISABLED=1 on the backend?")
    tok = login["access_token"]
    check("session is the constant demo visitor", login["user"]["username"] == "demo")
    status, me = req("GET", "/me", token=tok)
    check("/me answers from the token alone", status == 200 and me.get("id") == login["user"]["id"])

    print("2. the chip rail + replayed chat")
    status, chips = req("GET", "/demo/questions")
    check(
        "questions endpoint serves the pack",
        status == 200 and len(chips or []) >= 5,
        f"(count={len(chips or [])})",
    )
    chip_q = chips[0]["question"]
    status, ans = req("POST", "/ask", {"question": chip_q}, tok)
    check(
        "exact chip question answers",
        status == 200 and ans.get("engine") == "demo_replay",
        f"(engine={ans.get('engine') if ans else None})",
    )
    check("replay carries the recorded report", bool(ans.get("report")))

    print("3. nothing persists, nothing DB-backed is mounted")
    status, convs = req("GET", "/conversations", token=tok)
    check("conversations is empty", status == 200 and convs == [], f"(status={status})")
    status, _ = req(
        "POST",
        "/events",
        {"event_type": "demo_landing_view", "session_id": "smoke", "payload": {"visitor_id": "s"}},
    )
    check("event accepted (and dropped)", status == 201, f"(status={status})")
    for path in ("/admin/eval-goldens", "/explore/datasets", "/me/access", "/analytics/summary"):
        status, _ = req("GET", path, token=tok)
        check(f"GET {path} is 404 (router not mounted)", status == 404, f"(status={status})")
    status, _ = req("POST", "/sql", {"sql": "select 1"}, tok)
    check("POST /sql is 404", status == 404, f"(status={status})")
    status, _ = req("POST", "/admin/eval-goldens", {"question": "x", "dataset": "nsw_rent"}, tok)
    check("golden create is 404 (not 403)", status == 404, f"(status={status})")

    print("4. the exhibit tabs read the static dump from the frontend origin")
    key = f"exhibits/admin/eval-runs__limit={EVAL_RUNS_LIMIT}.json"
    status, ctype, body = fetch_static(f"{FRONTEND}/{key}")
    check(
        f"{key} is served as JSON",
        status == 200 and "json" in ctype and isinstance(body, list),
        f"(status={status}, type={ctype!r}, runs={len(body) if isinstance(body, list) else None})",
    )
    for key in (
        "exhibits/admin/eval-goldens__dataset=nsw_sales.json",
        "exhibits/admin/ops/summary__window=24h.json",
        "exhibits/architecture.json",
        "exhibits/me/access.json",
    ):
        status, ctype, body = fetch_static(f"{FRONTEND}/{key}")
        check(f"{key} is served as JSON", status == 200 and "json" in ctype and body is not None)
    status, ctype, _ = fetch_static(f"{FRONTEND}/exhibits/admin/does-not-exist.json")
    check(
        "a missing exhibit is not served as JSON (SPA fallback or 404)",
        status != 200 or "json" not in ctype,
        f"(status={status}, type={ctype!r})",
    )

    print()
    if _failed:
        sys.exit(f"DEMO SMOKE (DB-less) FAILED ✗  ({_passed} passed, {_failed} failed)")
    print(f"DEMO SMOKE (DB-less) PASSED ✓  ({_passed} checks)")


def main() -> None:
    if DB_DISABLED:
        main_db_disabled()
        return
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
    check(
        "questions endpoint serves the pack",
        status == 200 and len(chips or []) >= 5,
        f"(count={len(chips or [])})",
    )
    chip_q = chips[0]["question"]

    print("3. replayed chat")
    status, ans = req("POST", "/ask", {"question": chip_q}, tok)
    check(
        "exact chip question answers",
        status == 200 and ans.get("engine") == "demo_replay",
        f"(engine={ans.get('engine') if ans else None})",
    )
    check("replay carries the recorded report", bool(ans.get("report")))
    check("replay carries the recorded SQL", bool(ans.get("sql")))
    check("exact match has no closest-answer note", ans.get("demo_matched_question") in (None, ""))

    fuzzy = chip_q.replace("Which", "What").replace("?", "").lower()
    status, ans2 = req("POST", "/ask", {"question": fuzzy}, tok)
    check("fuzzy variant matches a recording", status == 200 and bool(ans2.get("sql")))

    status, miss = req("POST", "/ask", {"question": "write me a poem about kubernetes"}, tok)
    check(
        "unrelated question misses honestly",
        status == 200 and "recorded" in (miss.get("answer") or ""),
    )

    print("4. live SQL through the local governed executor")
    status, rows = req("POST", "/sql", {"sql": "SELECT * FROM marts.property_rent LIMIT 5"}, tok)
    ok_rows = (
        status == 200 and not (rows or {}).get("error") and (rows or {}).get("row_count", 0) > 0
    )
    check(
        "governed SELECT returns rows",
        ok_rows,
        f"(rows={(rows or {}).get('row_count')}, err={(rows or {}).get('error')})",
    )
    status, denied = req("POST", "/sql", {"sql": "DELETE FROM app.users"}, tok)
    check("write SQL is refused", status == 200 and bool((denied or {}).get("error")))
    status, cat = req("GET", "/schema/catalog", token=tok)
    tables = (cat or {}).get("tables") or []
    check(
        "schema catalog served locally",
        status == 200 and len(tables) > 0,
        f"(tables={len(tables)})",
    )
    check(
        "visitor catalog stays analytical (marts/staging only)",
        all(t.get("schema") in ("marts", "staging") for t in tables),
    )

    print("5. LLM endpoints are closed")
    status, _ = req("POST", "/sql/ai", {"action": "generate", "prompt": "top suburbs"}, tok)
    check("SQL AI assist 501s", status == 501)

    print("6. exhibits are readable, mutations are not")
    status, goldens = req("GET", "/admin/eval-goldens", token=tok)
    check(
        "goldens gallery readable by visitor",
        status == 200 and len(goldens or []) > 0,
        f"(count={len(goldens or [])})",
    )
    status, _ = req("GET", "/admin/events?limit=5", token=tok)
    check("admin events readable (static exhibit)", status == 200)
    status, _ = req("POST", "/admin/eval-goldens", {"question": "x", "dataset": "nsw_rent"}, tok)
    check("golden create still 403s", status == 403, f"(status={status})")
    status, _ = req("GET", "/analytics/summary", token=tok)
    check("analytics is owner-only (403 for visitor)", status == 403, f"(status={status})")

    print("7. analytics beacon")
    status, _ = req(
        "POST",
        "/events",
        {
            "event_type": "demo_landing_view",
            "session_id": "smoke",
            "payload": {"visitor_id": "smoke-visitor"},
        },
    )
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
