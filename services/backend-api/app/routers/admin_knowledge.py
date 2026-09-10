"""Knowledge curator (s49 M4, D3) — the backend-api proxy + write path.

Reads proxy to the data-agent's ``GET /agent/knowledge`` / ``GET
/agent/knowledge/{path}`` (``services/data-agent/agent/knowledge.py``), the
same httpx + shared-token pattern ``admin_pack.py`` uses for ``/admin/pack`` —
the data-agent has the knowledge markdown files on its filesystem, this
service does not.

The write (``PUT``), unlike the pack's, does NOT proxy through the data-agent:
migration 0039 grants ``INSERT``/``UPDATE`` on ``app.knowledge_pages`` to
``app_user`` only, and the data-agent's two DB roles (``agent_ro``,
``admin_ro`` — see ``services/data-agent/agent/config.py``) can both only
``SELECT`` it. ``app_user`` is this service's own role (``services/backend-api
/app/config.py``'s ``database_url``), so the write happens directly here —
the exact same shape ``routers/goldens.py``'s ``PUT
/admin/eval-goldens/ordinals`` already uses for the sibling curator table
``app.dataset_ordinals`` (migration 0036). The data-agent picks up the new row
on its own next ``load_overrides()`` call (a 5s TTL cache), so there is
nothing to invalidate across the service boundary — same as ordinals.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text

from ..auth import CurrentUser, admin_or_demo_read, require_admin
from ..config import settings
from ..db import jsonable, rls_connection
from ..limits import check_demo_ip_rate

router = APIRouter(tags=["admin"])
log = logging.getLogger("uvicorn.error")


def _agent_headers() -> dict[str, str]:
    """Shared-token auth for the agent hop (s12) — mirrors admin_pack.py."""
    headers: dict[str, str] = {}
    if settings.agent_shared_token:
        headers["X-Agent-Token"] = settings.agent_shared_token
    return headers


async def _agent_get(path: str) -> Any:
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=_agent_headers()) as client:
            resp = await client.get(f"{settings.agent_url}{path}")
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc
    except Exception as exc:  # noqa: BLE001 — surface as a clean 502, not a raw traceback
        raise HTTPException(status_code=502, detail=f"Agent unavailable: {exc}") from exc


@router.get("/admin/knowledge")
async def list_knowledge(
    request: Request, admin: CurrentUser = Depends(admin_or_demo_read)
) -> list[dict[str, Any]]:
    """Every knowledge page (path/name/description/source/version) — proxies
    the data-agent's file-tree + DB-override merge (it holds the files)."""
    check_demo_ip_rate(request, "admin_read", settings.demo_rate_admin_read_per_min)
    result: list[dict[str, Any]] = await _agent_get("/agent/knowledge")
    return result


@router.get("/admin/knowledge/{path:path}")
async def get_knowledge(
    request: Request, path: str, admin: CurrentUser = Depends(admin_or_demo_read)
) -> dict[str, Any]:
    """One page's effective body (DB override wins) — the edit box's seed value."""
    check_demo_ip_rate(request, "admin_read", settings.demo_rate_admin_read_per_min)
    result: dict[str, Any] = await _agent_get(f"/agent/knowledge/{path}")
    return result


class KnowledgePageIn(BaseModel):
    body: str
    author: str = ""


@router.put("/admin/knowledge/{path:path}")
async def put_knowledge(
    path: str, body: KnowledgePageIn, admin: CurrentUser = Depends(require_admin)
) -> dict[str, Any]:
    """Upsert a curator override for ``path`` — writes ``app.knowledge_pages``
    (bumping ``version``) plus an append-only ``app.knowledge_pages_log`` row,
    both as ``app_user`` (this service's DB role). The data-agent picks the
    new body up on its own next ``load_overrides()`` refresh (<=5s TTL); the
    caller doesn't need to poke it — the same fire-and-forget shape the
    ordinals curator endpoint already relies on."""
    author = body.author or admin.username or admin.email or admin.id
    async with rls_connection(admin.id) as conn:
        row = await conn.execute(
            text(
                "INSERT INTO app.knowledge_pages (path, name, body, version, author, updated_at) "
                "VALUES (:path, :name, :body, 1, :author, now()) "
                "ON CONFLICT (path) DO UPDATE SET "
                "  body = EXCLUDED.body, "
                "  version = app.knowledge_pages.version + 1, "
                "  author = EXCLUDED.author, "
                "  updated_at = now() "
                "RETURNING path, name, body, version, author, updated_at"
            ),
            # `name` only matters on first insert (a page authored purely
            # through the UI, no matching file yet) — the ON CONFLICT
            # branch never touches it, so an edit of a file-backed page
            # can't clobber the frontmatter-derived name with a guess.
            {
                "path": path,
                "name": path.rsplit("/", 1)[-1].removesuffix(".md"),
                "body": body.body,
                "author": author,
            },
        )
        saved = row.mappings().one()
        await conn.execute(
            text(
                "INSERT INTO app.knowledge_pages_log (path, version, body, author, action) "
                "VALUES (:path, :version, :body, :author, 'update')"
            ),
            {
                "path": saved["path"],
                "version": saved["version"],
                "body": saved["body"],
                "author": saved["author"],
            },
        )
    return {k: jsonable(v) for k, v in saved.items()}
