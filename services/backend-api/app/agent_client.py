from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, cast

import httpx

from .config import settings


def _headers() -> dict[str, str]:
    """Shared-token auth for the agent (s12): sent when configured, empty locally."""
    if settings.agent_shared_token:
        return {"X-Agent-Token": settings.agent_shared_token}
    return {}


async def ask_agent_stream(
    *, question: str, user_id: str, role: str, plan: str, dataset_slug: str
) -> AsyncIterator[dict[str, Any]]:
    """Stream a question to the data-agent's SSE endpoint.

    Yields ``{"event": <name>, "data": <parsed>}`` for each frame — ``progress``
    and ``status`` for live agent steps/heartbeats, ``plan``/``page`` for the s10
    page stream, then one ``result`` (the full AgentAnswer dict) or ``error``.
    The read timeout is generous: the agent's own 2s heartbeats keep bytes
    flowing, so a stalled step is what this guards.
    """
    payload = {
        "question": question,
        "user": {"id": user_id, "role": role, "plan": plan},
        "dataset_slug": dataset_slug,
    }
    timeout = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout, headers=_headers()) as client:
        async with client.stream(
            "POST", f"{settings.agent_url}/agent/ask/stream", json=payload
        ) as resp:
            resp.raise_for_status()
            buffer = ""
            async for chunk in resp.aiter_text():
                buffer += chunk
                # SSE frames are separated by a blank line.
                while "\n\n" in buffer:
                    frame, buffer = buffer.split("\n\n", 1)
                    event: str | None = None
                    data = ""
                    for line in frame.split("\n"):
                        if line.startswith("event: "):
                            event = line[7:].strip()
                        elif line.startswith("data: "):
                            data = line[6:]
                    if event is None:
                        continue
                    try:
                        parsed: Any = json.loads(data) if data else {}
                    except json.JSONDecodeError:
                        parsed = data
                    yield {"event": event, "data": parsed}


async def ask_agent(
    *, question: str, user_id: str, role: str, plan: str, dataset_slug: str
) -> dict[str, Any]:
    """Delegate a question to the data-agent service."""
    payload = {
        "question": question,
        "user": {"id": user_id, "role": role, "plan": plan},
        "dataset_slug": dataset_slug,
    }
    # A full insight report legitimately runs many tool round-trips (knowledge
    # search, several SQL queries, compute_trend, make_chart) and can take well
    # over a minute on a complex multi-entity question. 60s cut those off with a
    # 502 before the agent could even return its (possibly salvaged) report.
    async with httpx.AsyncClient(timeout=120.0, headers=_headers()) as client:
        resp = await client.post(f"{settings.agent_url}/agent/ask", json=payload)
        resp.raise_for_status()
        return cast(dict[str, Any], resp.json())


async def prep_golden(
    *,
    sql: str,
    code: str,
    user_id: str,
    role: str = "user",
    objects: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Golden authoring (s14 E1): run confirmed SQL through the governed extract,
    then (if code is given) the run_analysis script in the sandbox. Returns the
    extract rows + the produced report/skills so the Builder can draft Goal B.

    ``objects`` (s18) are named presentation objects recomputed against the same
    extract, so the Golden Sandbox repopulates built objects on load.
    """
    payload = {
        "sql": sql,
        "code": code,
        "objects": objects or [],
        "user": {"id": user_id, "role": role},
    }
    async with httpx.AsyncClient(timeout=120.0, headers=_headers()) as client:
        resp = await client.post(f"{settings.agent_url}/agent/analysis", json=payload)
        resp.raise_for_status()
        return cast(dict[str, Any], resp.json())


async def build_object(
    *,
    sql: str,
    name: str,
    object_type: str,
    spec: dict[str, Any],
    user_id: str,
    role: str = "user",
    instruction: str = "",
) -> dict[str, Any]:
    """Golden Sandbox (s18): deterministically build a NAMED presentation object
    from a structured spec (or an NL instruction) — the data-agent extends the
    shared extract as needed, runs the governed sandbox, and returns the lifted
    object + its generating code + the (possibly revised) SQL."""
    payload = {
        "sql": sql,
        "name": name,
        "object_type": object_type,
        "spec": spec,
        "instruction": instruction,
        "user": {"id": user_id, "role": role},
    }
    async with httpx.AsyncClient(timeout=120.0, headers=_headers()) as client:
        resp = await client.post(f"{settings.agent_url}/agent/analysis/build-object", json=payload)
        resp.raise_for_status()
        return cast(dict[str, Any], resp.json())


async def author_object(
    *,
    sql: str,
    code: str,
    object_type: str,
    instruction: str,
    user_id: str,
    role: str = "user",
    objects: list[dict[str, Any]] | None = None,
    target_element_id: str | None = None,
) -> dict[str, Any]:
    """Golden authoring (s14/s16): edit ONE report object from a plain-English
    instruction — the data-agent rewrites run_analysis to rebuild the WHOLE report
    (every object + the change), may revise the SQL when the data isn't in the
    extract, runs it, and returns the revised sql + full pages + the lifted target
    so the Builder keeps SQL/sandbox/data/presentation in sync.
    """
    payload = {
        "sql": sql,
        "code": code,
        "object_type": object_type,
        "instruction": instruction,
        "objects": objects or [],
        "target_element_id": target_element_id,
        "user": {"id": user_id, "role": role},
    }
    async with httpx.AsyncClient(timeout=120.0, headers=_headers()) as client:
        resp = await client.post(f"{settings.agent_url}/agent/analysis/object", json=payload)
        resp.raise_for_status()
        return cast(dict[str, Any], resp.json())


async def run_sql_on_agent(*, sql: str, user_id: str, role: str) -> dict[str, Any]:
    """Execute raw editor SQL via the data-agent's read-only, RLS-scoped executor."""
    payload = {"sql": sql, "user": {"id": user_id, "role": role}}
    async with httpx.AsyncClient(timeout=60.0, headers=_headers()) as client:
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
    async with httpx.AsyncClient(timeout=90.0, headers=_headers()) as client:
        resp = await client.post(f"{settings.agent_url}/agent/sql/assist", json=payload)
        resp.raise_for_status()
        return cast(dict[str, Any], resp.json())


async def fetch_agent_config() -> dict[str, Any]:
    """Fetch the data-agent's resolved config section (secrets already redacted there)."""
    async with httpx.AsyncClient(timeout=15.0, headers=_headers()) as client:
        resp = await client.get(f"{settings.agent_url}/agent/config")
        resp.raise_for_status()
        return cast(dict[str, Any], resp.json())


async def fetch_catalog(*, role: str) -> dict[str, Any]:
    """Fetch the structured schema catalog for the SQL editor's browser."""
    async with httpx.AsyncClient(timeout=30.0, headers=_headers()) as client:
        resp = await client.get(f"{settings.agent_url}/agent/schema", params={"role": role})
        resp.raise_for_status()
        return cast(dict[str, Any], resp.json())


async def fetch_skills() -> dict[str, Any]:
    """Fetch the sandbox skill catalog (s14 Golden Examples)."""
    async with httpx.AsyncClient(timeout=15.0, headers=_headers()) as client:
        resp = await client.get(f"{settings.agent_url}/agent/skills")
        resp.raise_for_status()
        return cast(dict[str, Any], resp.json())


async def scaffold_skills(
    *, question: str, columns: list[str], skills: list[str]
) -> dict[str, Any]:
    """Regenerate run_analysis code from selected skills, with reasoning (s14)."""
    payload = {"question": question, "columns": columns, "skills": skills}
    async with httpx.AsyncClient(timeout=90.0, headers=_headers()) as client:
        resp = await client.post(f"{settings.agent_url}/agent/skills/scaffold", json=payload)
        resp.raise_for_status()
        return cast(dict[str, Any], resp.json())
