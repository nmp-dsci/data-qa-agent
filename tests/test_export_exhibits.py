"""scripts/export_exhibits.py — the s52 exhibit key scheme + PII scrub.

Pure tests only (no stack): the key scheme is a contract with
frontend/src/lib/exhibits.ts, so both sides are pinned to the SAME vectors in
tests/exhibit_key_vectors.json (the TS half runs via `npm run test:exhibits`).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "export_exhibits.py"
VECTORS_PATH = REPO_ROOT / "tests" / "exhibit_key_vectors.json"

_spec = importlib.util.spec_from_file_location("export_exhibits", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
export_exhibits = importlib.util.module_from_spec(_spec)
sys.modules["export_exhibits"] = export_exhibits
_spec.loader.exec_module(export_exhibits)

VECTORS: list[tuple[str, str]] = [
    (path, key) for path, key in json.loads(VECTORS_PATH.read_text())["vectors"]
]


@pytest.mark.parametrize(("path", "expected"), VECTORS)
def test_exhibit_key_matches_shared_vectors(path: str, expected: str) -> None:
    assert export_exhibits.exhibit_key(path) == expected


def test_vectors_cover_the_routes_the_tabs_send() -> None:
    # Guard against the vectors file being trimmed to nothing.
    paths = {p for p, _ in VECTORS}
    assert {"/admin/eval-runs?limit=50", "/admin/eval-goldens/abc-123", "/me/access"} <= paths


def test_scrubber_maps_every_email_stably_and_blanks_events() -> None:
    dump = [
        {"username": "Nathan.P@Example.org", "email": "nathan.p@example.org", "x": 1},
        {"username": "admin", "email": "admin@example.com"},
        {"note": "contact aaa@b.co or nathan.p@example.org", "session_id": "s1", "ip": "1.2.3.4"},
    ]
    found: set[str] = set()
    export_exhibits.collect_emails(dump, found)
    assert found == {"nathan.p@example.org", "admin@example.com", "aaa@b.co"}
    scrubber = export_exhibits.Scrubber(found)
    # Sorted assignment: aaa@b.co -> 1, admin@example.com -> 2, nathan -> 3.
    assert scrubber.map["nathan.p@example.org"] == "user-3@example.com"
    out = scrubber.walk(dump)
    assert out[0]["username"] == "user-3@example.com"
    assert out[0]["email"] == "user-3@example.com"
    assert out[2]["note"] == "contact user-1@example.com or user-3@example.com"
    assert "session_id" not in out[2] and "ip" not in out[2]

    users = export_exhibits.scrub_users(
        [{"email": "nathan.p@example.org", "display_name": "Nathan P", "role": "admin"}], scrubber
    )
    assert users == [{"email": "user-3@example.com", "display_name": "User 3", "role": "admin"}]

    events = export_exhibits.scrub_events(
        [
            {
                "id": "e1",
                "event_type": "question_submitted",
                "username": "nathan.p@example.org",
                "payload": {"question": "secret free text", "visitor_id": "v", "ip": "10.0.0.1"},
            }
        ],
        scrubber,
    )
    assert events == [
        {
            "id": "e1",
            "event_type": "question_submitted",
            "username": "user-3@example.com",
            "payload": {},
        }
    ]


def test_slim_query_runs_keeps_only_the_band_fields() -> None:
    scrubber = export_exhibits.Scrubber([])
    rows = [{"id": "r", "created_at": "t", "trace": [{"huge": "x" * 1000}], "sql_text": "select"}]
    slim = export_exhibits.slim_query_runs(rows, scrubber)
    assert slim == [
        {k: ({"id": "r", "created_at": "t"}.get(k)) for k in export_exhibits._QUERY_RUN_7D_FIELDS}
    ]
    assert "trace" not in slim[0]
