"""Per-user agent memory (recall/remember), backed by app.user_memories (pgvector).

Recall is called programmatically before the agent runs (not an agent-invoked
tool) so it happens every turn regardless of model tool-calling discretion.
Remember stays a tool in llm_agent.py since deciding *what* is durably worth
remembering needs model judgment.

Both functions catch their own exceptions and degrade to a no-op. A broken or
ungranted memory table must not drag a good LLM+SQL answer down to the
offline stub via the outer catch-all in llm_agent.py — memory is a nice-to-have
personalization layer, not core functionality.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from .db import engine
from .embeddings import embed_text

_RECALL_MAX_DISTANCE = 0.6  # cosine distance cutoff — excludes unrelated memories
_REMEMBER_DEDUPE_DISTANCE = 0.05  # near-identical — skip inserting a duplicate


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(str(v) for v in vec) + "]"


async def recall_memories(user_id: str, query_text: str, k: int = 5) -> list[str]:
    try:
        vec = _vector_literal(await asyncio.to_thread(embed_text, query_text))
        async with engine.connect() as conn:
            async with conn.begin():
                await conn.execute(
                    text("SELECT set_config('app.current_user_id', :uid, true)"),
                    {"uid": user_id},
                )
                result = await conn.execute(
                    text(
                        "SELECT content FROM app.user_memories "
                        "WHERE (embedding <=> CAST(:vec AS vector)) < :max_dist "
                        "ORDER BY embedding <=> CAST(:vec AS vector) LIMIT :k"
                    ),
                    {"vec": vec, "max_dist": _RECALL_MAX_DISTANCE, "k": k},
                )
                return [row[0] for row in result.fetchall()]
    except Exception as exc:  # noqa: BLE001 — memory outage must not break the answer path
        print(f"[data-agent] recall_memories unavailable: {exc}")
        return []


async def remember_memory(user_id: str, fact: str) -> None:
    try:
        vec = _vector_literal(await asyncio.to_thread(embed_text, fact))
        async with engine.connect() as conn:
            async with conn.begin():
                await conn.execute(
                    text("SELECT set_config('app.current_user_id', :uid, true)"),
                    {"uid": user_id},
                )
                existing = await conn.execute(
                    text(
                        "SELECT 1 FROM app.user_memories "
                        "WHERE (embedding <=> CAST(:vec AS vector)) < :dedupe_dist LIMIT 1"
                    ),
                    {"vec": vec, "dedupe_dist": _REMEMBER_DEDUPE_DISTANCE},
                )
                if existing.first() is not None:
                    return
                await conn.execute(
                    text(
                        "INSERT INTO app.user_memories (user_id, kind, content, embedding) "
                        "VALUES (CAST(:uid AS uuid), 'preference', :content, CAST(:vec AS vector))"
                    ),
                    {"uid": user_id, "content": fact, "vec": vec},
                )
    except Exception as exc:  # noqa: BLE001 — memory outage must not break the answer path
        print(f"[data-agent] remember_memory unavailable: {exc}")
