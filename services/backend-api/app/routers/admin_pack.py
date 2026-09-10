"""Pack Inspector (s48 §P2) — the backend-api proxy.

Admin-only, thin pass-through to the data-agent's own ``GET /agent/pack`` /
``PUT /agent/pack/layouts/{id}`` (``services/data-agent/agent/pack_api.py``),
exactly the httpx + shared-token pattern ``routers/architecture.py`` already
uses. Response bodies are forwarded verbatim (``resp.json()``) rather than
re-modelled here, so the two services' contracts can't drift out from under
this proxy.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException

from ..auth import CurrentUser, require_admin
from ..config import settings

router = APIRouter(tags=["admin"])
log = logging.getLogger("uvicorn.error")


def _agent_headers() -> dict[str, str]:
    """Shared-token auth for the agent hop (s12) — the same header every other
    backend->agent call sends (see ``agent_client._headers``); kept local here
    rather than imported, matching ``routers/architecture.py``."""
    headers: dict[str, str] = {}
    if settings.agent_shared_token:
        headers["X-Agent-Token"] = settings.agent_shared_token
    return headers


@router.get("/admin/pack")
async def admin_pack(admin: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """The synced template pack + live thumbnails/staleness, for the Pack tab."""
    try:
        async with httpx.AsyncClient(timeout=30.0, headers=_agent_headers()) as client:
            resp = await client.get(f"{settings.agent_url}/agent/pack")
            resp.raise_for_status()
            result: dict[str, Any] = resp.json()
            return result
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc
    except Exception as exc:  # noqa: BLE001 — surface as a clean 502, not a raw traceback
        raise HTTPException(status_code=502, detail=f"Agent unavailable: {exc}") from exc


@router.put("/admin/pack/layouts/{layout_id}")
async def admin_pack_layout_update(
    layout_id: str,
    body: dict[str, Any] = Body(...),
    admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Enable/disable a layout or edit its ``use_when`` — writes the pack's
    Sheet, re-syncs pack.json, and returns the refreshed ``GET /admin/pack``
    payload (see ``agent/pack_api.py::update_pack_layout``)."""
    try:
        async with httpx.AsyncClient(timeout=60.0, headers=_agent_headers()) as client:
            resp = await client.put(
                f"{settings.agent_url}/agent/pack/layouts/{layout_id}", json=body
            )
            resp.raise_for_status()
            result: dict[str, Any] = resp.json()
            return result
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc
    except Exception as exc:  # noqa: BLE001 — surface as a clean 502, not a raw traceback
        raise HTTPException(status_code=502, detail=f"Agent unavailable: {exc}") from exc
