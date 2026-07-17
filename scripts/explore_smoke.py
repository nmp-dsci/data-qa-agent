"""Quick manual smoke for the /explore endpoints against a running stack.

    uv run python scripts/explore_smoke.py

Logs in as user1 (dev-auth), then hits every Explore endpoint and prints a
one-line PASS/FAIL per check. Not part of CI — a fast local confidence check.
"""

from __future__ import annotations

import json
import os
import sys
from urllib import error, request

API = os.environ.get("DATAQA_API_URL", "http://localhost:8000")


def _req(method: str, path: str, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = request.Request(API + path, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=30) as r:  # noqa: S310 - local only
            return r.status, json.loads(r.read())
    except error.HTTPError as e:
        return e.code, None


def main() -> int:
    ok = True

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal ok
        ok = ok and cond
        print(f"{'PASS' if cond else 'FAIL'}  {name}  {extra}")

    st, login = _req("POST", "/auth/dev-login", {"username": "user1"})
    if st != 200:
        print(f"login failed ({st})")
        return 2
    token = login["access_token"]

    st, ds = _req("GET", "/explore/datasets", token=token)
    slugs = [d["slug"] for d in (ds or {}).get("datasets", [])]
    check("GET /explore/datasets lists 3 datasets", st == 200 and set(slugs) >= {"nsw_sales", "nsw_rent", "nsw_yield"}, str(slugs))

    st, agg = _req(
        "POST",
        "/explore/aggregate",
        {"dataset": "nsw_yield", "metrics": ["gross_yield_pct", "n_sold"], "group_by": ["property_type"]},
        token,
    )
    check("POST /explore/aggregate (yield by type)", st == 200 and agg and agg["row_count"] > 0, str(agg and agg["rows"][:2]))

    st, agg2 = _req(
        "POST",
        "/explore/aggregate",
        {"dataset": "nsw_rent", "metrics": ["avg_weekly_rent"], "group_by": ["sa3_region"]},
        token,
    )
    check("POST /explore/aggregate (rent by SA3 geo join)", st == 200 and agg2 and agg2["row_count"] > 0)

    st, prof = _req(
        "POST",
        "/explore/profile",
        {
            "dataset": "nsw_rent",
            "target": {"filters": {"year": 2023}},
            "comparison": {"filters": {"year": 2022}},
        },
        token,
    )
    check(
        "POST /explore/profile (2023 vs 2022 rent)",
        st == 200 and prof and prof.get("predictors") is not None,
        f"predictors={len(prof.get('predictors', [])) if prof else 0}",
    )

    st, ask = _req(
        "POST",
        "/explore/ask",
        {"question": "compare FY2022 and FY2021 weekly rent for houses", "mode": "profile"},
        token,
    )
    good = st == 200 and ask and ask["state"].get("dataset") == "nsw_rent"
    check("POST /explore/ask (NL -> profile state)", good, json.dumps(ask.get("state") if ask else None))

    # RLS: user2 (no grants) gets an empty dataset list.
    st2, login2 = _req("POST", "/auth/dev-login", {"username": "user2"})
    if st2 == 200:
        st, ds2 = _req("GET", "/explore/datasets", token=login2["access_token"])
        check("RLS: user2 sees no datasets", st == 200 and len((ds2 or {}).get("datasets", [])) == 0)

    print("\n" + ("ALL EXPLORE CHECKS PASSED" if ok else "SOME EXPLORE CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
