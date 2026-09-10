"""Pack Inspector (s48 §P2) — the backend-api proxy.

Handler-level (no server, no database), mirroring test_architecture_router.py's
style: this router owns nothing but the admin gate and the httpx hop, so the
one behaviour worth testing here is that a data-agent failure surfaces as a
clean HTTPException rather than a raw traceback.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import HTTPException

from app.auth import CurrentUser
from app.config import settings
from app.routers import admin_pack


@pytest.fixture
def admin() -> CurrentUser:
    return CurrentUser(
        id="00000000-0000-0000-0000-000000000000", username="admin", email="a@x.test", role="admin"
    )


@pytest.fixture
def unreachable_agent(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Port 1 refuses connections instantly on loopback, so this exercises the
    # real httpx connect-failure path with no mocking and no dependence on any
    # service actually being down.
    monkeypatch.setattr(settings, "agent_url", "http://127.0.0.1:1")
    yield


async def test_admin_pack_502s_when_agent_unreachable(
    admin: CurrentUser, unreachable_agent: None
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await admin_pack.admin_pack(admin=admin)
    assert exc_info.value.status_code == 502
    assert "Agent unavailable" in str(exc_info.value.detail)


async def test_admin_pack_layout_update_502s_when_agent_unreachable(
    admin: CurrentUser, unreachable_agent: None
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await admin_pack.admin_pack_layout_update(
            "L2", body={"use_when": "a new sentence"}, admin=admin
        )
    assert exc_info.value.status_code == 502
