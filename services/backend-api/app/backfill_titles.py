#!/usr/bin/env python3
"""Backfill conversation titles (s17 E1).

Existing conversations were titled with the raw first question truncated to 60
chars, producing dozens of near-identical sidebar entries ("show me trend of
sale pric…"). This retitles them with the same short agent-generated summaries
new conversations now get on their first answer.

Run inside the backend-api container (it reuses the backend's DB + agent client):

    docker compose exec backend-api python -m app.backfill_titles            # placeholders only
    docker compose exec backend-api python -m app.backfill_titles --all      # every conversation
    docker compose exec backend-api python -m app.backfill_titles --dry-run  # show, don't write

RLS: candidates are read under an admin's context (the conversations/messages
policies grant admins read-all); each title is written under its owner's context
because the conversations policy's WITH CHECK requires user_id = the caller.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from app.agent_client import title_agent
from app.db import engine, rls_connection

_CANDIDATES = """
SELECT c.id, c.user_id, m.content
FROM app.conversations c
JOIN LATERAL (
    SELECT content
    FROM app.messages
    WHERE conversation_id = c.id AND role = 'user'
    ORDER BY created_at ASC
    LIMIT 1
) m ON true
{where}
ORDER BY c.created_at DESC
"""


async def main() -> None:
    ap = argparse.ArgumentParser(description="Retitle conversations with agent summaries.")
    ap.add_argument(
        "--all", action="store_true", help="retitle every conversation, not just placeholders"
    )
    ap.add_argument("--dry-run", action="store_true", help="print proposed titles without writing")
    args = ap.parse_args()

    # app.users has no RLS — read the admin id directly to bootstrap the context.
    async with engine.connect() as conn:
        admin_id = (
            await conn.execute(
                text("SELECT id FROM app.users WHERE role = 'admin' ORDER BY created_at LIMIT 1")
            )
        ).scalar()
    if admin_id is None:
        print("No admin user found — cannot read conversations across users.")
        return

    where = "" if args.all else "WHERE c.title IS NULL OR c.title = left(m.content, 60)"
    async with rls_connection(str(admin_id)) as conn:
        rows = (await conn.execute(text(_CANDIDATES.format(where=where)))).all()

    print(f"{len(rows)} conversation(s) to retitle{' (dry run)' if args.dry_run else ''}.")
    updated = 0
    for cid, uid, question in rows:
        try:
            title = (await title_agent(question or "")).strip()
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {cid}: title failed ({exc})")
            continue
        if not title:
            continue
        if args.dry_run:
            print(f"  {str(question)[:44]!r} -> {title!r}")
            continue
        async with rls_connection(str(uid)) as conn:
            await conn.execute(
                text("UPDATE app.conversations SET title = :t WHERE id = :cid"),
                {"t": title[:120], "cid": str(cid)},
            )
        updated += 1
        print(f"  ✓ {title}")

    await engine.dispose()
    print(f"Done — {updated} conversation(s) retitled.")


if __name__ == "__main__":
    asyncio.run(main())
