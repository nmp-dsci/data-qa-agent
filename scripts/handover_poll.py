#!/usr/bin/env python3
"""Handover poller — did anyone open the deck, and what did they change? (s48 §7)

For every run that produced a deck within the last ``--days`` and hasn't been
checked in the last ``--interval`` seconds: ask Drive for the file's current
``version``; if it moved past what we last saw, pull the deck/sheet content,
normalise it (``agent.handover.normalise_deck``/``normalise_sheet``), diff it
against the previous snapshot (``agent.handover.diff_snapshots``), and record
what changed. Read-only against the user's files — this never writes to Drive.

Run from ``services/data-agent`` so the ``agent`` package and its deps resolve:

    cd services/data-agent && uv run python ../../scripts/handover_poll.py --once

``--once`` runs a single pass and exits (cron/compose-friendly); with no flag
it loops every ``--interval`` seconds. Needs ``ADMIN_RO_DATABASE_URL`` (or the
default compose value) and the same ``GOOGLE_DECK_*`` credential the deck
builder uses — nothing new.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_AGENT_DIR = REPO_ROOT / "services" / "data-agent"
if str(DATA_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_AGENT_DIR))

from agent.db import admin_engine  # noqa: E402
from agent.gsuite import (  # noqa: E402
    DRIVE_API,
    GoogleApiError,
    GoogleClient,
    credentials_present,
    quote_a1_tab,
)
from agent.handover import diff_snapshots, normalise_deck, normalise_sheet  # noqa: E402
from agent.handover_snapshot import snapshot_chart_types  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncConnection  # noqa: E402

DEFAULT_DAYS = 30
DEFAULT_INTERVAL_S = 900  # 15 minutes — matches §7 and the compose service's loop

# Drive Activity API v2 — best-effort actor attribution (§7 item 4). Not part
# of GoogleClient: the credential's granted scopes are drive.file/spreadsheets/
# presentations only (scripts/google_auth.py SCOPES), which does NOT itself
# grant driveactivity.readonly, so this call is expected to 403 until a human
# deliberately re-consents the credential with that scope added. See
# docs/template-packs.md §7.
DRIVE_ACTIVITY_QUERY_URL = "https://driveactivity.googleapis.com/v2/activity:query"


def _file_id_from_url(url: str) -> str | None:
    """``.../d/<id>/edit`` -> ``<id>``, for both Slides and Sheets URLs."""
    parts = url.split("/d/")
    if len(parts) != 2:
        return None
    return parts[1].split("/")[0] or None


async def _file_meta(client: GoogleClient, file_id: str) -> dict[str, Any]:
    """``files.get(fields=version,modifiedTime,name)`` — not on GoogleClient yet
    (the deck builder never needed it), so this calls the shared transport
    directly rather than waiting on that."""
    return await client._call(  # noqa: SLF001 — the one sanctioned direct use
        "GET", f"{DRIVE_API}/{file_id}", params={"fields": "version,modifiedTime,name"}
    )


async def _candidates(conn: AsyncConnection, days: int, interval_s: int) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    "SELECT id, message_id, artifact_deck_url, artifact_sheet_url, "
                    "artifact_last_checked, artifact_opened_at, artifact_edit_count, created_at "
                    "FROM app.query_runs "
                    "WHERE artifact_deck_url IS NOT NULL "
                    "  AND created_at >= now() - make_interval(days => :days) "
                    "  AND (artifact_last_checked IS NULL "
                    "       OR artifact_last_checked < now() - make_interval(secs => :interval_s)) "
                    "ORDER BY created_at DESC"
                ),
                {"days": days, "interval_s": interval_s},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def _manifest_for(conn: AsyncConnection, message_id: str | None) -> dict[str, Any] | None:
    if not message_id:
        return None
    report = await conn.execute(
        text("SELECT report FROM app.messages WHERE id = :mid"), {"mid": message_id}
    )
    row = report.first()
    if row is None or row[0] is None:
        return None
    artifact = (row[0] or {}).get("artifact")
    return artifact if isinstance(artifact, dict) else None


async def _last_snapshot(conn: AsyncConnection, run_id: str, kind: str) -> dict[str, Any] | None:
    row = (
        (
            await conn.execute(
                text(
                    "SELECT snapshot, drive_version, taken_at FROM app.artifact_snapshots "
                    "WHERE run_id = :rid AND kind = :kind ORDER BY drive_version DESC LIMIT 1"
                ),
                {"rid": run_id, "kind": kind},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def _actor_since(client: GoogleClient, file_id: str, since: datetime | None) -> str | None:
    """Who touched ``file_id`` after ``since``, or ``None`` when the Activity
    API can't answer (no ``since`` bound at all is not queried — an unbounded
    query on a long-lived deck could be large and the caller always has a
    ``taken_at`` to filter on after the first snapshot).

    Best-effort only: a 403 (missing scope), a network error, or an unexpected
    response shape all resolve to ``None`` rather than failing the poll — actor
    attribution is a nice-to-have on top of the edit log, never a reason to
    drop or delay recording the edit itself.
    """
    if since is None:
        return None
    body: dict[str, Any] = {
        "itemName": f"items/{file_id}",
        "filter": f'time > "{since.isoformat()}"',
    }
    try:
        out = await client._call(  # noqa: SLF001 — same sanctioned direct use as _file_meta
            "POST", DRIVE_ACTIVITY_QUERY_URL, json=body
        )
    except Exception:  # noqa: BLE001 — 403 (scope missing) is the expected case today
        return None
    for activity in out.get("activities") or []:
        for actor in activity.get("actors") or []:
            known = (actor.get("user") or {}).get("knownUser") or {}
            if known.get("isCurrentUser"):
                continue  # the generating account itself is not "a human editor"
            name = str(known.get("personName") or "").strip()
            if name:
                return name
    return None


def _tab_of_range(table_range: str) -> str:
    if "!" not in table_range:
        return ""
    return table_range.split("!", 1)[0].strip("'")


async def _sheet_values(
    client: GoogleClient, spreadsheet_id: str, manifest: dict[str, Any]
) -> tuple[dict[str, list[list[Any]]], set[str]]:
    """Returns ``(values_by_tab, failed_tabs)`` — ``failed_tabs`` is every tab
    a read raised on, so the caller can carry the prior snapshot's values
    forward for it instead of treating a transient error as an empty table."""
    tabs: set[str] = set()
    for slide in manifest.get("slides") or []:
        tab = _tab_of_range(str(slide.get("table_range") or ""))
        if tab:
            tabs.add(tab)
    if not tabs:
        tabs = {"Data"}
    values: dict[str, list[list[Any]]] = {}
    failed: set[str] = set()
    for tab in tabs:
        try:
            values[tab] = await client.read_values(spreadsheet_id, f"{quote_a1_tab(tab)}!A1:ZZ5000")
        except GoogleApiError as exc:
            print(f"    could not read tab {tab!r}: {exc}", file=sys.stderr)
            failed.add(tab)
    return values, failed


async def _poll_kind(
    conn: AsyncConnection,
    client: GoogleClient,
    *,
    run_id: str,
    kind: str,
    url: str,
    manifest: dict[str, Any],
    charts_before: dict[int, dict[str, str]] | None = None,
    charts_after: dict[int, dict[str, str]] | None = None,
    actor: str | None = None,
) -> tuple[int, datetime | None]:
    """Snapshot + diff one artifact (deck or sheet) for one run. Returns
    (new edit count, opened_at if newly detected).

    ``charts_before``/``charts_after`` are only meaningful for ``kind ==
    "deck"`` — a chart's type is a Sheets property (§7 item 3), so the caller
    fetches it once per run from the spreadsheet and passes it in here rather
    than this function reaching across kinds itself.
    """
    file_id = _file_id_from_url(url)
    if not file_id:
        return 0, None
    meta = await _file_meta(client, file_id)
    current_version = int(meta.get("version") or 0)
    modified_time = _parse_ts(meta.get("modifiedTime"))

    prior = await _last_snapshot(conn, run_id, kind)
    if prior is not None and current_version <= int(prior["drive_version"]):
        return 0, None  # no Drive-side change since we last looked

    if kind == "deck":
        presentation_id = manifest.get("presentation_id") or file_id
        after = normalise_deck(await client.get_presentation(presentation_id))
    else:
        spreadsheet_id = manifest.get("spreadsheet_id") or file_id
        values_by_tab, failed_tabs = await _sheet_values(client, spreadsheet_id, manifest)
        if failed_tabs and prior is None:
            # No stored baseline yet, and at least one tab failed to read: an
            # empty table here is not "the deck shipped with zero rows", it's
            # a transient read failure. Writing it as the permanent version-1
            # baseline would poison every future diff with a fabricated
            # table_rows_added once the real rows show up — so write nothing
            # and let the next poll retry the baseline from scratch.
            print(
                f"  run {run_id}: {kind} first-observation read failed for "
                f"{sorted(failed_tabs)}, skipping baseline this pass"
            )
            return 0, None
        after = normalise_sheet(values_by_tab, manifest)
        if failed_tabs and prior is not None:
            # A failed read is not a deletion — carry that table's prior
            # value forward unchanged so it neither fabricates a diff nor
            # loses what we last knew; the next poll retries the real read.
            prior_tables_by_name = {
                t.get("name"): t for t in (prior["snapshot"].get("tables") or [])
            }
            for slide in manifest.get("slides") or []:
                name = slide.get("table_name")
                tab = _tab_of_range(str(slide.get("table_range") or ""))
                fallback = prior_tables_by_name.get(name) if name and tab in failed_tabs else None
                if fallback is None:
                    continue
                for table in after["tables"]:
                    if table.get("name") == name:
                        table["header"] = fallback.get("header", [])
                        table["rows"] = fallback.get("rows", [])
                        break
        # The sheet snapshot also carries the current chart-type map (§7 item
        # 3), so next poll's "before" is available without a second fetch.
        after["charts"] = charts_after or {}

    # No stored baseline (a run built before ask.py persisted version-1
    # snapshots): the first observation BECOMES the baseline and emits nothing.
    # The manifest alone cannot say what charts, notes or table rows the deck
    # shipped with, so diffing against it reports the builder's own output as
    # human edits — which is exactly the false signal this log must not carry.
    if prior is None:
        events = []
    elif kind == "deck":
        events = diff_snapshots(
            prior["snapshot"],
            after,
            manifest,
            charts_before=charts_before,
            charts_after=charts_after,
        )
    else:
        events = diff_snapshots(prior["snapshot"], after, manifest)

    await conn.execute(
        text(
            "INSERT INTO app.artifact_snapshots "
            "(run_id, kind, file_id, drive_version, modified_time, snapshot) "
            "VALUES (:rid, :kind, :fid, :ver, :mtime, CAST(:snap AS jsonb)) "
            "ON CONFLICT (run_id, kind, drive_version) DO NOTHING"
        ),
        {
            "rid": run_id,
            "kind": kind,
            "fid": file_id,
            "ver": current_version,
            "mtime": modified_time,
            "snap": _dumps(after),
        },
    )
    for ev in events:
        await conn.execute(
            text(
                "INSERT INTO app.artifact_edits "
                "(run_id, kind, event, layout_id, slide_index, slide_object_id, table_name, "
                " before, after, actor, drive_version) "
                "VALUES (:rid, :kind, :event, :layout_id, :slide_index, :slide_object_id, "
                " :table_name, CAST(:before AS jsonb), CAST(:after AS jsonb), :actor, :ver)"
            ),
            {
                "rid": run_id,
                "kind": kind,
                "event": ev["event"],
                "layout_id": ev.get("layout_id"),
                "slide_index": ev.get("slide_index"),
                "slide_object_id": ev.get("slide_object_id"),
                "table_name": ev.get("table_name"),
                "before": _dumps(ev.get("before")),
                "after": _dumps(ev.get("after")),
                "actor": actor,
                "ver": current_version,
            },
        )

    # "opened" = a real version bump past the version-1 baseline — not "had an
    # edit". A visitor who opened and closed without changing anything still
    # bumps Slides' revision on some interactions; we don't try to be cleverer
    # than that, we just never claim "opened" on a version that never moved.
    opened_at: datetime | None = None
    if prior is None and current_version > 1:
        opened_at = modified_time
    elif prior is not None:
        opened_at = modified_time
    return len(events), opened_at


def _dumps(value: Any) -> str | None:
    return json.dumps(value) if value is not None else None


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def poll_once(*, days: int, interval_s: int) -> dict[str, int]:
    stats = {"candidates": 0, "checked": 0, "decks_touched": 0, "edits_recorded": 0, "errors": 0}
    if not credentials_present():
        print(
            "GOOGLE_DECK_CLIENT_ID/_SECRET/_REFRESH_TOKEN not set — nothing to poll.",
            file=sys.stderr,
        )
        return stats

    client = GoogleClient()
    async with admin_engine.begin() as conn:
        candidates = await _candidates(conn, days, interval_s)
    stats["candidates"] = len(candidates)

    for run in candidates:
        run_id = str(run["id"])
        stats["checked"] += 1
        try:
            async with admin_engine.begin() as conn:
                manifest = await _manifest_for(conn, run.get("message_id"))
                if manifest is None:
                    print(f"  run {run_id}: no artifact manifest in messages.report, skipping")
                    await conn.execute(
                        text(
                            "UPDATE app.query_runs SET artifact_last_checked = now() "
                            "WHERE id = :rid"
                        ),
                        {"rid": run_id},
                    )
                    continue

                # Fetched once per run, up front, and reused across both
                # kinds: charts_after is the spreadsheet's live chart types
                # (needed by the deck diff and stashed on the sheet snapshot
                # for next time); charts_before is whatever the last sheet
                # snapshot stashed there. Actor attribution is similarly a
                # single Activity API call per run, not per kind.
                prior_sheet = await _last_snapshot(conn, run_id, "sheet")
                # jsonb round-trips dict keys as strings, so a chart_id stored
                # last poll comes back as "9" — restore int keys to match the
                # deck snapshot's own chart_id type before diff_snapshots
                # tries to look one up by the other.
                charts_before: dict[int, dict[str, str]] = {}
                if prior_sheet is not None:
                    for key, spec in (prior_sheet["snapshot"].get("charts") or {}).items():
                        try:
                            charts_before[int(key)] = spec
                        except (TypeError, ValueError):
                            continue

                spreadsheet_id = manifest.get("spreadsheet_id")
                # Defaults to the prior chart map, not empty: a transient
                # fetch failure must not zero out every tracked chart's type
                # on the next diff — only a successful fetch replaces it.
                charts_after: dict[int, dict[str, str]] = dict(charts_before)
                if spreadsheet_id:
                    try:
                        charts_after = await snapshot_chart_types(client, spreadsheet_id)
                    except GoogleApiError as exc:
                        print(f"  run {run_id}: chart-type fetch skipped, keeping prior map: {exc}")

                activity_file_id = _file_id_from_url(
                    run.get("artifact_deck_url") or run.get("artifact_sheet_url") or ""
                )
                actor: str | None = None
                if activity_file_id:
                    actor = await _actor_since(
                        client, activity_file_id, run.get("artifact_last_checked")
                    )

                total_new_events = 0
                opened_at: datetime | None = None
                for kind, url in (
                    ("deck", run.get("artifact_deck_url")),
                    ("sheet", run.get("artifact_sheet_url")),
                ):
                    if not url:
                        continue
                    new_events, kind_opened_at = await _poll_kind(
                        conn,
                        client,
                        run_id=run_id,
                        kind=kind,
                        url=url,
                        manifest=manifest,
                        charts_before=charts_before,
                        charts_after=charts_after,
                        actor=actor,
                    )
                    total_new_events += new_events
                    if kind_opened_at and not opened_at:
                        opened_at = kind_opened_at
                if total_new_events:
                    stats["decks_touched"] += 1
                    stats["edits_recorded"] += total_new_events

                already_opened = run.get("artifact_opened_at") is not None
                await conn.execute(
                    text(
                        "UPDATE app.query_runs SET "
                        "  artifact_last_checked = now(), "
                        "  artifact_edit_count = artifact_edit_count + :new_events"
                        + (
                            ", artifact_opened_at = :opened_at"
                            if opened_at and not already_opened
                            else ""
                        )
                        + " WHERE id = :rid"
                    ),
                    {
                        "rid": run_id,
                        "new_events": total_new_events,
                        **({"opened_at": opened_at} if opened_at and not already_opened else {}),
                    },
                )
        except GoogleApiError as exc:
            stats["errors"] += 1
            print(f"  run {run_id}: Google API error: {exc}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 — one bad run must not kill the pass
            stats["errors"] += 1
            print(f"  run {run_id}: {type(exc).__name__}: {exc}", file=sys.stderr)

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--once", action="store_true", help="run a single pass and exit")
    parser.add_argument(
        "--days", type=int, default=DEFAULT_DAYS, help="candidate window (default 30)"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL_S,
        help="seconds between passes (default 900)",
    )
    args = parser.parse_args()

    async def _run_loop() -> None:
        while True:
            started = time.time()
            print(f"[{datetime.now(UTC).isoformat()}] handover poll starting")
            stats = await poll_once(days=args.days, interval_s=args.interval)
            print(f"  {stats}")
            if args.once:
                return
            elapsed = time.time() - started
            await asyncio.sleep(max(1.0, args.interval - elapsed))

    asyncio.run(_run_loop())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
