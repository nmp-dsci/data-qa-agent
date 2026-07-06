from __future__ import annotations

from typing import Any, cast

import httpx

from .config import settings


async def ask_agent(*, question: str, user_id: str, role: str, dataset_slug: str) -> dict[str, Any]:
    """Delegate a question to the data-agent service."""
    payload = {
        "question": question,
        "user": {"id": user_id, "role": role},
        "dataset_slug": dataset_slug,
    }
    # A full insight report legitimately runs many tool round-trips (knowledge
    # search, several SQL queries, compute_trend, make_chart) and can take well
    # over a minute on a complex multi-entity question. 60s cut those off with a
    # 502 before the agent could even return its (possibly salvaged) report.
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(f"{settings.agent_url}/agent/ask", json=payload)
        resp.raise_for_status()
        return cast(dict[str, Any], resp.json())


async def run_sql_on_agent(*, sql: str, user_id: str, role: str) -> dict[str, Any]:
    """Execute raw editor SQL via the data-agent's read-only, RLS-scoped executor."""
    payload = {"sql": sql, "user": {"id": user_id, "role": role}}
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(f"{settings.agent_url}/agent/sql", json=payload)
        resp.raise_for_status()
        return cast(dict[str, Any], resp.json())


async def assist_sql_on_agent(
    *, action: str, prompt: str | None, sql: str | None, user_id: str, role: str
) -> dict[str, Any]:
    """Ask the data-agent to generate/explain/fix/optimize SQL (Phase C AI assist)."""
    payload = {
        "action": action,
        "prompt": prompt,
        "sql": sql,
        "user": {"id": user_id, "role": role},
    }
    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(f"{settings.agent_url}/agent/sql/assist", json=payload)
        resp.raise_for_status()
        return cast(dict[str, Any], resp.json())


async def fetch_agent_config() -> dict[str, Any]:
    """Fetch the data-agent's resolved config section (secrets already redacted there)."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{settings.agent_url}/agent/config")
        resp.raise_for_status()
        return cast(dict[str, Any], resp.json())


async def fetch_catalog(*, role: str) -> dict[str, Any]:
    """Fetch the structured schema catalog for the SQL editor's browser."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{settings.agent_url}/agent/schema", params={"role": role})
        resp.raise_for_status()
        return cast(dict[str, Any], resp.json())
