#!/usr/bin/env python3
"""Snapshot a template pack's two Google files into ``packs/<name>/pack.json`` (s48 §9).

    uv run --project services/data-agent python scripts/pack_sync.py --name nsw-property
    uv run --project services/data-agent python scripts/pack_sync.py --check   # CI

Thin CLI wrapper — the actual read/validate/write logic lives in
``agent.pack_sync.sync_pack`` (and ``build_pack_dict`` for the ``--check`` path
below, which must not write anything), refactored out of this script (s48 §P2)
so the Pack Inspector's ``PUT /agent/pack/layouts/{id}`` handler can call the
exact same code a curator running this script would. **Nothing else may write
pack.json**: it is a snapshot, never a source of truth.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "services" / "data-agent"))

from agent.gsuite import GoogleClient, credentials_present  # noqa: E402
from agent.pack_sync import build_pack_dict, sync_pack, without_timestamp  # noqa: E402


def run(*, name: str, version: int, check: bool) -> int:
    if not credentials_present():
        print("Missing GOOGLE_DECK_* credentials.", file=sys.stderr)
        return 2

    client = GoogleClient()
    out = REPO / "packs" / name / "pack.json"

    if check:
        pack_dict = asyncio.run(build_pack_dict(client, name=name, version=version))
        text = json.dumps(pack_dict, indent=2, sort_keys=False) + "\n"
        current = out.read_text(encoding="utf-8") if out.exists() else ""
        if without_timestamp(current) != without_timestamp(text):
            print(f"{out} is stale — run scripts/pack_sync.py", file=sys.stderr)
            return 1
        print(f"{out} is up to date")
        return 0

    spec = asyncio.run(sync_pack(client, name=name, version=version, pack_dir=REPO / "packs"))
    print(f"wrote {out}")
    _report(spec)
    return 0


def _report(pack: Any) -> None:
    for issue in pack.issues:
        print(f"  ! {issue}")
    for layout in pack.layouts:
        state = "on " if layout.enabled else "off"
        slots = ", ".join(sorted(layout.slots))
        print(f"  [{state}] {layout.id:<3} {layout.name:<20} slots: {slots or '—'}")
        for issue in layout.issues:
            print(f"        ! {issue}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="nsw-property")
    parser.add_argument("--version", type=int, default=1)
    parser.add_argument("--check", action="store_true", help="exit non-zero if pack.json is stale")
    args = parser.parse_args()
    return run(name=args.name, version=args.version, check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
