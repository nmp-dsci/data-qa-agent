"""Parity harness: legacy chronicle cube vs the live /explore/aggregate.

The legacy static profiler pre-computed an OLAP cube (docs/chronicle/datafeed/
profile_nswrent.json etc.) from the full CSVs, scoped to a small region. This
script replays a slice of that cube against the new aggregate endpoint and checks
the ADDITIVE metrics (counts and sums) match exactly — the derived averages then
follow. It is a LOCAL / MANUAL gate, not part of CI:

  * it needs the FULL data loaded (`make pipeline-full`), because the legacy JSON
    was built from the full CSVs, not the committed sample; and
  * it needs the gitignored `docs/chronicle/datafeed/*.json` present (see
    docs/chronicle/README.md to restore them).

Usage:
    # bring the stack up with full data, then:
    API=http://localhost:5230 TOKEN=<dev-jwt> uv run python scripts/explore_parity.py

The token must belong to a user granted nsw_rent (e.g. user1). The legacy rent
cube is scoped to the Newcastle SA3, 2012-2021; we reproduce that with a
sa3_region filter and compare the total bond count and summed weekly rent.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib import request

ROOT = Path(__file__).resolve().parents[1]
DATAFEED = ROOT / "docs" / "chronicle" / "datafeed"


def _load_legacy_cube(path: Path) -> list[dict]:
    raw = path.read_text()
    m = re.search(r"=\s*(\[.*\])\s*$", raw, re.S)
    return json.loads(m.group(1) if m else raw.split("=", 1)[1])


def _legacy_totals(cube: list[dict]) -> tuple[int, float]:
    """The 'all/all/all' base cut (cutid 0): total bonds and summed rent."""
    base = [r for r in cube if r.get("agg0_v") == "0" and r.get("agg1_c") == "all"]
    n = sum(int(r.get("rental_n_v", 0)) for r in base)
    total = sum(float(r.get("rent_n_v", 0)) for r in base)
    return n, total


def _post(api: str, token: str, path: str, body: dict) -> dict:
    req = request.Request(
        f"{api}{path}",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=30) as resp:  # noqa: S310 - local dev only
        return json.loads(resp.read())


def main() -> int:
    api = os.environ.get("API", "http://localhost:5230")
    token = os.environ.get("TOKEN")
    if not token:
        print("set TOKEN to a dev JWT for a user granted nsw_rent", file=sys.stderr)
        return 2
    cube_path = DATAFEED / "profile_nswrent.json"
    if not cube_path.exists():
        print(f"legacy cube missing: {cube_path} (see docs/chronicle/README.md)", file=sys.stderr)
        return 3

    cube = _load_legacy_cube(cube_path)
    legacy_n, legacy_total = _legacy_totals(cube)

    # Live aggregate for the same Newcastle SA3 scope.
    resp = _post(
        api,
        token,
        "/explore/aggregate",
        {
            "dataset": "nsw_rent",
            "metrics": ["n_rented", "total_weekly_rent"],
            "group_by": [],
            "filters": {"sa3_region": "Newcastle"},
        },
    )
    row = resp["rows"][0]
    live_n, live_total = int(row[0]), float(row[1])

    print(f"legacy: n={legacy_n} total={legacy_total:.0f}")
    print(f"live:   n={live_n} total={live_total:.0f}")
    ok = legacy_n == live_n and abs(legacy_total - live_total) < 1.0
    print("PARITY OK" if ok else "PARITY MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
