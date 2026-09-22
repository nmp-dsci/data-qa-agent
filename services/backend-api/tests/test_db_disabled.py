"""s52: the DB-less demo contract.

The deployed demo runs with DEMO_MODE=1 DB_DISABLED=1 and no Postgres at all
(transcript-rag-agent shape). Router mounting is decided at import time, so
the app is exercised in a child interpreter with the flags in its environment
and DATABASE_URL pointing at a closed port — any code path that still reaches
for an engine fails loudly instead of being masked by the dev database.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_CHILD = r"""
import json
from fastapi.testclient import TestClient
from app.main import app

out = {}
with TestClient(app) as c:
    out["health"] = c.get("/health").json()
    out["health_db"] = c.get("/health/db", headers={"X-Client-Channel": "web"}).json()
    out["auth_config"] = c.get("/auth/config").json()
    login = c.post("/auth/demo-login")
    out["login_status"] = login.status_code
    tok = login.json()["access_token"]
    out["login_user"] = login.json()["user"]
    h = {"Authorization": f"Bearer {tok}"}
    out["me"] = c.get("/me", headers=h).json()
    out["me_bogus"] = c.get("/me", headers={"Authorization": "Bearer bogus"}).status_code
    out["conversations"] = c.get("/conversations", headers=h).json()
    out["messages"] = c.get("/conversations/anything/messages", headers=h).json()
    out["event"] = c.post("/events", json={"event_type": "page_view"}).status_code
    qs = c.get("/demo/questions", headers=h).json()
    out["questions"] = len(qs)
    ask = c.post("/ask", json={"question": qs[0]["question"]}, headers=h)
    out["ask_status"] = ask.status_code
    body = ask.json()
    out["ask_has_report"] = bool(body.get("report"))
    out["ask_ids"] = [bool(body.get(k)) for k in ("conversation_id", "message_id", "run_id")]
    out["unmounted"] = {
        p: c.get(p, headers=h).status_code
        for p in ("/admin/eval-goldens", "/admin/eval-runs", "/explore/datasets", "/me/access")
    }
    out["sql"] = c.post("/sql", json={"sql": "select 1"}, headers=h).status_code
    out["mcp_anon"] = c.post("/mcp").status_code
    out["mcp_key"] = c.post(
        "/mcp", headers={"Authorization": "Bearer dpk_abcdefgh_" + "x" * 32}
    ).status_code
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def child() -> dict[str, Any]:
    env = {
        **os.environ,
        "DEMO_MODE": "1",
        "DB_DISABLED": "1",
        "DATABASE_URL": "postgresql+asyncpg://x:x@127.0.0.1:1/x",
        "ADMIN_RO_DATABASE_URL": "postgresql+asyncpg://x:x@127.0.0.1:1/x",
        "AGENT_RO_DATABASE_URL": "postgresql+asyncpg://x:x@127.0.0.1:1/x",
        "AGENT_URL": "",
        # The deployed shape: google mode with no client id (no owner door).
        "AUTH_MODE": "google",
        "GOOGLE_CLIENT_ID": "",
    }
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    parsed: dict[str, Any] = json.loads(proc.stdout.strip().splitlines()[-1])
    return parsed


def test_health_reports_disabled_db(child: dict[str, Any]) -> None:
    assert child["health"] == {"status": "ok", "env": "dev"}
    assert child["health_db"] == {"status": "disabled", "env": "dev"}
    assert child["auth_config"]["auth_mode"] == "demo"


def test_demo_login_and_me_need_no_users_table(child: dict[str, Any]) -> None:
    assert child["login_status"] == 200
    user = child["login_user"]
    assert user["username"] == "demo" and user["role"] == "user"
    assert child["me"]["id"] == user["id"]
    assert child["me_bogus"] == 401


def test_chat_replays_without_persisting(child: dict[str, Any]) -> None:
    assert child["conversations"] == [] and child["messages"] == []
    assert child["event"] == 201
    assert child["questions"] > 0
    assert child["ask_status"] == 200 and child["ask_has_report"] is True
    assert child["ask_ids"] == [True, True, True]


def test_db_backed_surfaces_are_not_mounted(child: dict[str, Any]) -> None:
    assert set(child["unmounted"].values()) == {404}
    assert child["sql"] == 404


def test_mcp_gate_rejects_without_touching_db(child: dict[str, Any]) -> None:
    assert child["mcp_anon"] == 401
    assert child["mcp_key"] == 401
