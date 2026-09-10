"""Knowledge curator (s49 M4, D3) — the backend-api proxy + write path.

Handler-level (no server), mirroring test_admin_pack_router.py's style: the
GET side owns nothing but the admin gate and the httpx hop to the data-agent
(it holds the knowledge files), so the behaviour worth testing here is a
clean 502 rather than a raw traceback when that hop fails. The PUT write path
against app.knowledge_pages is exercised live (docker compose + curl) rather
than against a real DB in this unit suite — same as goldens.py's ordinals
endpoint, which has no DB-backed unit test either.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import HTTPException

from app.auth import CurrentUser
from app.config import settings
from app.routers import admin_knowledge


@pytest.fixture
def admin() -> CurrentUser:
    return CurrentUser(
        id="00000000-0000-0000-0000-000000000000", username="admin", email="a@x.test", role="admin"
    )


@pytest.fixture
def unreachable_agent(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Port 1 refuses connections instantly on loopback — exercises the real
    # httpx connect-failure path with no mocking, no live service required.
    monkeypatch.setattr(settings, "agent_url", "http://127.0.0.1:1")
    yield


async def test_list_knowledge_502s_when_agent_unreachable(
    admin: CurrentUser, unreachable_agent: None
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await admin_knowledge.list_knowledge(request=None, admin=admin)  # type: ignore[arg-type]
    assert exc_info.value.status_code == 502
    assert "Agent unavailable" in str(exc_info.value.detail)


async def test_get_knowledge_502s_when_agent_unreachable(
    admin: CurrentUser, unreachable_agent: None
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await admin_knowledge.get_knowledge(
            request=None,  # type: ignore[arg-type]
            path="domains/property-rent/overview.md",
            admin=admin,
        )
    assert exc_info.value.status_code == 502
    assert "Agent unavailable" in str(exc_info.value.detail)


def test_knowledge_page_in_defaults_author_to_empty_string() -> None:
    body = admin_knowledge.KnowledgePageIn(body="some text")
    assert body.author == ""


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd.md",
        "../../etc/passwd.md",
        "domains/../../../etc/passwd.md",
        "domains/property-rent/overview.py",
        "domains\\property-rent\\overview.md",
        "",
    ],
)
def test_validate_knowledge_path_rejects_escapes_and_non_markdown(path: str) -> None:
    with pytest.raises(HTTPException) as exc_info:
        admin_knowledge._validate_knowledge_path(path)
    assert exc_info.value.status_code == 400


def test_validate_knowledge_path_allows_a_plain_relative_md_path() -> None:
    path = "domains/property-rent/bedrooms.md"
    assert admin_knowledge._validate_knowledge_path(path) == path
