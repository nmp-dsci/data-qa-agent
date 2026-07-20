"""Resolve the agent build fingerprint to an ``app.agent_versions`` row (s24 M1).

Every ``app.query_runs`` row carries the id of the build that produced it, so an
answer — and any eval score derived from it — is attributable to an exact
provider/model/prompt/skills/knowledge combination. That is what makes a
base-vs-experiment comparison able to claim "one lever moved".

The fingerprint only changes when the agent is rebuilt or reconfigured, so it is
cached in-process and refreshed lazily. Resolution is strictly best-effort: a
data-agent that is slow or down must never cost the user their answer, so every
failure path returns ``None`` and the run is simply written unstamped.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from .agent_client import fetch_agent_version

log = logging.getLogger(__name__)

# fingerprint -> agent_versions.id. Unbounded in principle, but bounded in
# practice by how many distinct builds one process sees before restarting.
_ID_CACHE: dict[str, str] = {}
# The last fingerprint payload fetched from the agent, so a stamp costs one HTTP
# call per TTL rather than one per question.
_CACHED: dict[str, Any] | None = None
_CACHED_AT: float = 0.0

# The agent can be redeployed independently of the backend — which is exactly
# what an eval experiment does, changing one lever and restarting the agent
# alone. Caching for the process lifetime would then stamp every subsequent run
# with the *previous* build, silently attributing an experiment's results to the
# baseline. A short TTL keeps the stamp honest at negligible cost.
_TTL_SECONDS = 30.0


def reset_cache() -> None:
    """Drop the memoised build identity — used by tests, and after a redeploy."""
    global _CACHED, _CACHED_AT
    _CACHED = None
    _CACHED_AT = 0.0
    _ID_CACHE.clear()


async def _upsert(conn: AsyncConnection, fp: dict[str, Any]) -> str:
    """Insert the build if new, and return its id either way.

    ``fingerprint`` is uniquely constrained (migration 0020), so concurrent
    writers converge on one row: the DO UPDATE keeps the RETURNING clause
    populated on conflict, which a bare DO NOTHING would not.
    """
    row = await conn.execute(
        text(
            "INSERT INTO app.agent_versions "
            "(fingerprint, label, provider, model_id, prompt_hash, skills_hash, "
            " knowledge_version, image_tag, git_sha) "
            "VALUES (:fingerprint, :label, :provider, :model_id, :prompt_hash, "
            " :skills_hash, :knowledge_version, :image_tag, :git_sha) "
            "ON CONFLICT (fingerprint) DO UPDATE SET label = EXCLUDED.label "
            "RETURNING id"
        ),
        {
            "fingerprint": fp.get("fingerprint", ""),
            "label": fp.get("label", ""),
            "provider": fp.get("provider", ""),
            "model_id": fp.get("model_id", ""),
            "prompt_hash": fp.get("prompt_hash", ""),
            "skills_hash": fp.get("skills_hash", ""),
            "knowledge_version": fp.get("knowledge_version", ""),
            "image_tag": fp.get("image_tag", ""),
            "git_sha": fp.get("git_sha", ""),
        },
    )
    return str(row.scalar_one())


async def current_agent_version_id(conn: AsyncConnection) -> str | None:
    """The ``app.agent_versions.id`` for the agent build serving requests now.

    Returns ``None`` when the build cannot be determined — the caller stamps
    nothing rather than failing. ``agent_version_id`` is nullable precisely so
    that this degrades instead of breaking.
    """
    global _CACHED, _CACHED_AT
    try:
        if _CACHED is None or (time.monotonic() - _CACHED_AT) > _TTL_SECONDS:
            _CACHED = await fetch_agent_version()
            _CACHED_AT = time.monotonic()
        fingerprint = str(_CACHED.get("fingerprint") or "")
        if not fingerprint:
            return None
        cached_id = _ID_CACHE.get(fingerprint)
        if cached_id is not None:
            return cached_id
        version_id = await _upsert(conn, _CACHED)
        _ID_CACHE[fingerprint] = version_id
        return version_id
    except Exception as exc:  # noqa: BLE001 - never fail an answer over provenance
        log.warning("agent version unavailable, run will not be stamped: %s", exc)
        _CACHED = None
        return None
