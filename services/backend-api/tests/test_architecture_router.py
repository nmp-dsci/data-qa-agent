"""Architecture tab (M5, agent_sdk migration) — the backend-api proxy.

Handler-level (no server, no database), mirroring test_demo_mode.py's style.
Exercises the one behaviour this router owns beyond a plain pass-through: it
must degrade cleanly rather than 500 when the data-agent is unreachable — the
same situation a demo deployment is in permanently (s38 P4, no data-agent App
Runner service there) and any deployment is in transiently on a restart.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import HTTPException

from app.auth import CurrentUser
from app.config import settings
from app.routers import architecture


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


async def test_architecture_degrades_when_agent_unreachable(
    admin: CurrentUser, unreachable_agent: None
) -> None:
    result = await architecture.architecture(admin=admin)
    assert result["available"] is False
    assert "could not reach data-agent" in result["error"]


async def test_architecture_content_502s_when_agent_unreachable(
    admin: CurrentUser, unreachable_agent: None
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await architecture.architecture_content(kind="marts", name="", admin=admin)
    assert exc_info.value.status_code == 502
