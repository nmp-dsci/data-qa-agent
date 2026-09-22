#!/usr/bin/env python3
"""Export the demo's static exhibits from the local dev stack (s52).

The deployed demo runs with no database (``DEMO_MODE=1 DB_DISABLED=1``), so
the read-only exhibit tabs — Goldens, Evaluations, Operations, Architecture,
Admin, and Settings' access panel — cannot read from the API there. This
script logs into the running LOCAL dev stack as the admin, fetches every
route those tabs read (with the exact query strings the frontend sends,
enumerating detail routes from the list responses), scrubs PII, and writes
each response to ``frontend/public/exhibits/`` under the key scheme shared
with ``frontend/src/lib/exhibits.ts``::

    exhibits/<path without leading slash>[__<canonical query>].json

``exhibit_key`` below is that scheme's Python half; ``tests/test_export_exhibits.py``
pins both halves to ``tests/exhibit_key_vectors.json``.

Run with the dev backend up (Postgres, AUTH_MODE=dev)::

    make export-exhibits            # == uv run python scripts/export_exhibits.py

then commit the generated files: the frontend bundle ships them
(``scripts/deploy_frontend.sh`` syncs ``frontend/dist`` wholesale).

PII: every email address anywhere in the dump becomes ``user-N@example.com``
(one N per distinct email, numbered in sorted order so re-exports are
stable); ``/admin/users`` display names become ``User N``; ``/admin/events``
payloads are blanked (the Admin tab renders only type/time/user) and any
IPv4 literal in those two routes is zeroed; ``session_id`` / ``ip`` /
``user_agent`` keys are dropped at any depth. Stdlib only.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "frontend" / "public" / "exhibits"

API = os.environ.get(
    "EXPORT_API_URL",
    os.environ.get("SMOKE_API_URL", f"http://localhost:{os.environ.get('API_HOST_PORT', '8010')}"),
)
ADMIN_USER = os.environ.get("EXPORT_ADMIN_USER", "admin")
# The identity the Settings tab's "My data access" panel is exported as: the
# demo visitor sees the demo user's grants, not the admin's.
VISITOR_USER = os.environ.get("EXPORT_VISITOR_USER", "demo")
TIMEOUT = int(os.environ.get("EXPORT_TIMEOUT_S", "120"))

# Mirrors frontend/src/lib/exhibits.ts — keep the four constants in lockstep.
EXHIBIT_ROOT = "exhibits"
VOLATILE_QUERY_KEYS = frozenset({"since"})
_SAFE = re.compile(r"[^A-Za-z0-9._=&-]")
# The Goldens tab's fallback dataset list (GoldensPage.tsx FALLBACK_DATASETS):
# export a list for each even if the registry no longer serves the slug.
FALLBACK_DATASETS = ("nsw_sales", "nsw_rent")
OPS_WINDOWS = ("24h", "7d", "28d")


# ---- key scheme (contract with lib/exhibits.ts) ------------------------------


def _sanitise(s: str) -> str:
    return _SAFE.sub("_", s)


def exhibit_key(path: str) -> str:
    """The static file key for a GET ``path`` (optionally with a query string).

    Path segments are URL-decoded, re-split on ``/`` and sanitised; the query
    is sorted by key, ``k=v`` pairs joined by ``&`` on DECODED values, then
    sanitised. Volatile params (``since``) are dropped. No leading slash.
    """
    raw_path, _, raw_query = path.partition("?")
    decoded = "/".join(urllib.parse.unquote(seg) for seg in raw_path.split("/"))
    segments = [_sanitise(seg) for seg in decoded.split("/") if seg]

    pairs: list[tuple[str, str]] = []
    for part in raw_query.split("&"):
        if not part:
            continue
        k, _, v = part.partition("=")
        k = urllib.parse.unquote_plus(k)
        v = urllib.parse.unquote_plus(v)
        if k in VOLATILE_QUERY_KEYS:
            continue
        pairs.append((k, v))
    pairs.sort(key=lambda kv: kv[0])
    canonical = _sanitise("&".join(f"{k}={v}" for k, v in pairs))

    base = f"{EXHIBIT_ROOT}/{'/'.join(segments)}"
    return f"{base}__{canonical}.json" if canonical else f"{base}.json"


# ---- PII scrub ---------------------------------------------------------------

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_DROP_KEYS = frozenset({"session_id", "ip", "ip_address", "client_ip", "remote_addr", "user_agent"})


def collect_emails(value: Any, found: set[str]) -> None:
    if isinstance(value, str):
        found.update(m.lower() for m in _EMAIL.findall(value))
    elif isinstance(value, dict):
        for v in value.values():
            collect_emails(v, found)
    elif isinstance(value, list):
        for v in value:
            collect_emails(v, found)


class Scrubber:
    """Stable email -> ``user-N@example.com`` mapping over one whole dump."""

    def __init__(self, emails: Iterable[str]) -> None:
        self.map = {e: f"user-{i}@example.com" for i, e in enumerate(sorted(set(emails)), 1)}

    def number(self, email: str) -> int:
        return int(self.map[email.lower()].split("-")[1].split("@")[0])

    def text(self, s: str) -> str:
        return _EMAIL.sub(lambda m: self.map.get(m.group(0).lower(), "user-0@example.com"), s)

    def walk(self, value: Any, *, zero_ips: bool = False) -> Any:
        if isinstance(value, str):
            out = self.text(value)
            return _IPV4.sub("0.0.0.0", out) if zero_ips else out
        if isinstance(value, dict):
            return {
                k: self.walk(v, zero_ips=zero_ips) for k, v in value.items() if k not in _DROP_KEYS
            }
        if isinstance(value, list):
            return [self.walk(v, zero_ips=zero_ips) for v in value]
        return value


def scrub_users(rows: list[dict[str, Any]], scrubber: Scrubber) -> list[dict[str, Any]]:
    """``/admin/users``: placeholder email, ``User N`` display name, no IPs."""
    out = []
    for r in rows:
        email = str(r.get("email") or "")
        n = scrubber.number(email) if email.lower() in scrubber.map else 0
        row = dict(r)
        row["display_name"] = f"User {n}" if n else "User"
        out.append(scrubber.walk(row, zero_ips=True))
    return out


def scrub_events(rows: list[dict[str, Any]], scrubber: Scrubber) -> list[dict[str, Any]]:
    """``/admin/events``: the Admin tab renders type/time/user only — the raw
    payload (question text, visitor ids, reasons) is blanked wholesale."""
    out = []
    for r in rows:
        row = dict(r)
        if "payload" in row:
            row["payload"] = {}
        out.append(scrubber.walk(row, zero_ips=True))
    return out


_QUERY_RUN_7D_FIELDS = (
    "id",
    "created_at",
    "username",
    "dataset",
    "engine",
    "source",
    "channel",
    "status",
    "row_count",
    "latency_ms",
)


def slim_query_runs(rows: list[dict[str, Any]], scrubber: Scrubber) -> Any:
    """The Admin band's 7-day ``limit=2000`` fetch reads only ``created_at``
    (a count + sparkline); the full traces would be tens of MB."""
    return scrubber.walk([{k: r.get(k) for k in _QUERY_RUN_7D_FIELDS} for r in rows])


def scrub_default(body: Any, scrubber: Scrubber) -> Any:
    return scrubber.walk(body)


Transform = Callable[[Any, Scrubber], Any]
Route = tuple[str, str, Transform, "Api"]


# ---- HTTP --------------------------------------------------------------------


class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.token: str | None = None

    def login(self, username: str) -> None:
        status, body = self._request("POST", "/auth/dev-login", {"username": username})
        if status != 200 or not body or not body.get("access_token"):
            sys.exit(
                f"dev-login as {username!r} failed ({status}) — is the DEV backend up at "
                f"{self.base} (AUTH_MODE=dev, Postgres)? DEMO/DB_DISABLED stacks cannot export."
            )
        self.token = body["access_token"]

    def get(self, path: str) -> Any:
        status, body = self._request("GET", path)
        if status != 200:
            raise RuntimeError(f"GET {path} -> {status}: {json.dumps(body)[:200]}")
        return body

    def _request(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json", "X-Client-Channel": "web"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.status, json.loads(resp.read() or "null")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or "null")
            except Exception:  # noqa: BLE001
                return e.code, None
        except urllib.error.URLError as e:
            sys.exit(f"cannot reach {self.base}: {e.reason}")


# ---- the route plan -----------------------------------------------------------


def _q(params: dict[str, str]) -> str:
    return urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


def plan_routes(api: Api, visitor: Api) -> list[Route]:
    """(fetch path, key path, transform, client) for every exhibit the demo tabs read.

    The key path differs from the fetch path only where a volatile ``since``
    param is involved — ``exhibit_key`` drops it either way, so both spellings
    land on the same file; keeping them separate here just documents it.
    """
    since_7d = (datetime.now(UTC) - timedelta(days=7)).isoformat()
    routes: list[Route] = []

    def add(path: str, transform: Transform = scrub_default, client: Api = api) -> None:
        routes.append((path, path, transform, client))

    # Goldens tab
    datasets = api.get("/admin/datasets")
    slugs = [d["slug"] for d in datasets if d.get("slug")]
    add("/admin/datasets")
    add("/explore/datasets")
    add("/admin/eval-goldens")
    golden_ids: list[str] = []
    for slug in dict.fromkeys([*slugs, *FALLBACK_DATASETS]):
        path = f"/admin/eval-goldens?{_q({'dataset': slug})}"
        add(path)
        golden_ids += [g["id"] for g in api.get(path)]
    for gid in dict.fromkeys(golden_ids):
        add(f"/admin/eval-goldens/{gid}")

    # Evaluations tab
    add("/admin/eval-runs?limit=50")
    for run in api.get("/admin/eval-runs?limit=50"):
        add(f"/admin/eval-runs/{run['id']}")

    # Operations tab
    for w in OPS_WINDOWS:
        add(f"/admin/ops/summary?window={w}")
    add("/admin/ops/runs?limit=20")

    # Architecture tab
    add("/architecture")
    arch = api.get("/architecture")
    for f in (arch.get("knowledge") or {}).get("files") or []:
        add(f"/architecture/content?{_q({'kind': f['kind'], 'name': f.get('id') or ''})}")
        if f["kind"] == "knowledge":
            add(f"/admin/knowledge/{urllib.parse.quote(f['id'], safe='')}")
    add("/admin/knowledge")
    add("/admin/query-runs?limit=50")

    # Admin tab
    add("/admin/query-runs")
    routes.append(
        (
            f"/admin/query-runs?{_q({'since': since_7d, 'limit': '2000'})}",
            "/admin/query-runs?limit=2000",
            slim_query_runs,
            api,
        )
    )
    add("/admin/events", scrub_events)
    routes.append(
        (
            f"/admin/events?{_q({'since': since_7d, 'limit': '2000'})}",
            "/admin/events?limit=2000",
            scrub_events,
            api,
        )
    )
    add("/admin/users", scrub_users)
    add("/admin/feedback")
    add("/admin/eval-cases")
    add("/admin/config")
    add("/admin/pack")

    # Settings tab — as the visitor, whose grants are what a demo session sees.
    add("/me/access", client=visitor)
    return routes


# ---- main --------------------------------------------------------------------


def main() -> None:
    api = Api(API)
    api.login(ADMIN_USER)
    visitor = Api(API)
    visitor.login(VISITOR_USER)
    print(f"exporting exhibits from {API} as {ADMIN_USER!r} (access panel as {VISITOR_USER!r})")

    routes = plan_routes(api, visitor)

    # Pass 1: fetch everything, collect every email for the stable placeholder map.
    fetched: list[tuple[str, Transform, Any]] = []
    emails: set[str] = set()
    for fetch_path, key_path, transform, client in routes:
        body = client.get(fetch_path)
        collect_emails(body, emails)
        fetched.append((key_path, transform, body))
    scrubber = Scrubber(emails)

    # Pass 2: transform + scrub + write.
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)
    rows: list[tuple[str, str, int]] = []
    manifest: list[dict[str, Any]] = []
    for key_path, transform, body in fetched:
        body = transform(body, scrubber)
        key = exhibit_key(key_path)
        target = REPO_ROOT / "frontend" / "public" / key
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        target.write_text(payload + "\n", encoding="utf-8")
        size = len(payload.encode("utf-8")) + 1
        rows.append((key_path, key, size))
        manifest.append({"route": key_path, "file": key, "bytes": size})

    total = sum(r[2] for r in rows)
    (OUT_DIR / "_manifest.json").write_text(
        json.dumps(
            {
                "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "api": API,
                "routes": len(rows),
                "bytes": total,
                "emails_scrubbed": len(scrubber.map),
                "files": manifest,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    width = max(len(r[0]) for r in rows)
    print()
    print(f"{'route':<{width}}  {'file':<{width + 16}}  bytes")
    for route, key, size in rows:
        print(f"{route:<{width}}  {key:<{width + 16}}  {size:>9,}")
    print()
    print(
        f"{len(rows)} routes, {total / 1_000_000:.2f} MB -> {OUT_DIR.relative_to(REPO_ROOT)}/ "
        f"({len(scrubber.map)} distinct emails scrubbed)"
    )
    if total > 15_000_000:
        print("WARNING: dump exceeds the ~15 MB budget — trim the heaviest routes above.")


if __name__ == "__main__":
    main()
