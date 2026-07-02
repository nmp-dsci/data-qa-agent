from __future__ import annotations

from typing import Any

import httpx

from .config import settings


async def ask_agent(*, question: str, user_id: str, role: str, dataset_slug: str) -> dict[str, Any]:
    """Delegate a question to the data-agent service."""
    payload = {
        "question": question,
        "user": {"id": user_id, "role": role},
        "dataset_slug": dataset_slug,
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(f"{settings.agent_url}/agent/ask", json=payload)
        resp.raise_for_status()
        return resp.json()
