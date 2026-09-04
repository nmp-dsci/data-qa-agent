"""Architecture tab (M5, agent_sdk migration) — a live snapshot of the GenAI
system for the Data Pilot UI itself: the pipeline, the knowledge base, the
tool registry, and (via the frontend reusing ``/admin/query-runs``) a
walk-through of a real run's trace.

Admin-only, read-only, adds no new persistence: both routes below simply
proxy the data-agent's own ``/agent/architecture*`` endpoints, which read
settings, ``sdk_agent``'s tool definitions, and the knowledge tree directly —
never building a per-run workspace. The run walk-through deliberately reuses
the existing ``GET /admin/query-runs`` (services/backend-api/app/routers/
events.py) rather than adding a parallel endpoint here: that route already
returns id/question/engine/status/created_at + the full trace jsonb for
recent runs, admin-gated the same way, so a second implementation would only
be able to drift from it.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException

from ..auth import CurrentUser, require_admin
from ..config import settings

router = APIRouter(tags=["architecture"])
log = logging.getLogger("uvicorn.error")


def _agent_headers() -> dict[str, str]:
    """Shared-token auth for the agent hop (s12) — the same header every other
    backend->agent call sends (see ``agent_client._headers``); kept local here
    rather than imported so this file stays a self-contained new router."""
    headers: dict[str, str] = {}
    if settings.agent_shared_token:
        headers["X-Agent-Token"] = settings.agent_shared_token
    return headers


@router.get("/architecture")
async def architecture(admin: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Runtime, knowledge base index, and tool registry from the live data-agent.

    Degrades rather than 500s when the agent is unreachable — a demo
    deployment has no data-agent service at all (s38 P4) — so the tab still
    renders whatever it can (the system map still draws; the live-data panels
    show the reason instead of a broken page).
    """
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=_agent_headers()) as client:
            resp = await client.get(f"{settings.agent_url}/agent/architecture")
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
        data["available"] = True
        return data
    except Exception as exc:  # noqa: BLE001 — degrade, don't break the tab
        log.warning("architecture: could not reach data-agent: %s", exc)
        return {
            "available": False,
            "error": f"could not reach data-agent ({settings.agent_url})",
        }


@router.get("/architecture/content")
async def architecture_content(
    kind: str,
    name: str = "",
    admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """One knowledge-base file's body, for the browser's detail pane.

    ``kind``/``name`` are passed straight through to the data-agent — see
    ``GET /agent/architecture/content`` for what each ``kind`` means.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=_agent_headers()) as client:
            resp = await client.get(
                f"{settings.agent_url}/agent/architecture/content",
                params={"kind": kind, "name": name},
            )
            resp.raise_for_status()
            result: dict[str, Any] = resp.json()
            return result
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc
    except Exception as exc:  # noqa: BLE001 — surface as a clean 502, not a raw traceback
        raise HTTPException(status_code=502, detail=f"Agent unavailable: {exc}") from exc
