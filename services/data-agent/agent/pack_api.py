"""Pack Inspector — the admin-facing read/edit surface over the synced
template pack (s48 §P2, `.lavish/s48_template-pack-plan.html`).

``GET /agent/pack`` renders ``packs/<name>/pack.json`` (agent/pack.py) plus two
things pack.json can't carry on its own: a thumbnail per library slide (a
Slides ``pages.getThumbnail`` call — the expensive read, cached in-process
keyed on the Slides file's Drive ``version`` so a GET never repeats it) and
``stale`` (whether the Sheet has been edited more recently than the last sync).

``PUT /agent/pack/layouts/{id}`` is the only mutation: it writes the curator's
``enabled``/``use_when`` change into the matching row of the pack's ``_pack``
tab (only the changed cells, via Sheets ``values.update``), then runs the exact
same sync ``scripts/pack_sync.py`` runs (``agent.pack_sync.sync_pack``) so
pack.json — the only thing the runtime reads — is never hand-edited out of
sync with what's actually in Google. It also busts ``sdk_agent``'s in-process
catalogue caches, so the very next run sees the change, and reports
``fingerprint_changed`` because editing ``layouts.md`` moves the agent's
``av-*`` build fingerprint (see ``agent/version.py``).

Both endpoints degrade to "no Google credentials" rather than fail: a
deployment without ``GOOGLE_DECK_*`` never registers the deck-export tools
either (see ``gsuite.credentials_present``), and this tab must still show the
curator what pack.json already says.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .config import settings
from .gsuite import GoogleClient, credentials_present, deck_url
from .gsuite import sheet_url as sheet_edit_url
from .pack import LayoutSpec, PackSpec, load_pack, pack_path
from .pack_sync import PACK_TAB, sync_pack


class PackApiError(RuntimeError):
    """A Pack Inspector request could not be completed."""


class PackLayoutNotFound(PackApiError):
    """The `_pack` tab has no row for this layout id."""


class PackUnavailable(PackApiError):
    """No synced pack.json, or no Google credentials to edit it with."""


class PackLayoutOut(BaseModel):
    id: str
    name: str
    enabled: bool
    use_when: str
    source: str
    slots: list[str]
    table_template: str
    chart_template: str | None = None  # "<tab>!<title>", for display only
    grader_shape: str
    issues: list[str] = []
    thumbnail_url: str | None = None


class PackOut(BaseModel):
    name: str
    version: int
    synced_at: str
    slides_url: str = ""
    sheet_url: str = ""
    folder_url: str = ""
    layouts: list[PackLayoutOut] = []
    issues: list[str] = []
    stale: bool = False


class PackLayoutUpdate(BaseModel):
    enabled: bool | None = None
    use_when: str | None = None


class PackLayoutUpdateOut(PackOut):
    fingerprint_changed: bool = True


# Keyed "<slides_id>:<drive_version>:<slide_object_id>" -> contentUrl ("" on a
# fetch failure, cached the same as a real thumbnail so one bad slide can't
# turn every GET into a retry storm). One process-wide dict, exactly like
# sdk_agent's own catalogue cache — a pack is a property of the deployment,
# not of a request.
_thumb_cache: dict[str, str] = {}


def get_google_client() -> GoogleClient:
    """FastAPI dependency: a real client in prod, a FakeClient via
    ``app.dependency_overrides`` in tests."""
    return GoogleClient()


def _folder_url(folder_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{folder_id}" if folder_id else ""


def _chart_template_label(layout: LayoutSpec) -> str | None:
    ct = layout.chart_template
    if not ct:
        return None
    return f"{ct.get('tab', '')}!{ct.get('title', '')}"


def _layout_out(layout: LayoutSpec, thumbnail_url: str | None) -> PackLayoutOut:
    return PackLayoutOut(
        id=layout.id,
        name=layout.name,
        enabled=layout.enabled,
        use_when=layout.use_when,
        source=layout.source,
        slots=sorted(layout.slots),
        table_template=layout.table_template,
        chart_template=_chart_template_label(layout),
        grader_shape=layout.grader_shape,
        issues=list(layout.issues),
        thumbnail_url=thumbnail_url,
    )


async def _thumbnails(client: GoogleClient, pack: PackSpec) -> dict[str, str | None]:
    """layout id -> thumbnail contentUrl (None when there's no library slide,
    "" when the fetch itself failed — both render as a placeholder)."""
    out: dict[str, str | None] = {}
    if not pack.slides_id:
        return out
    try:
        meta = await client.file_meta(pack.slides_id, fields="version")
        version = str(meta.get("version") or "0")
    except Exception:  # noqa: BLE001 — a version lookup failure just skips caching
        version = "0"
    for layout in pack.layouts:
        if not layout.slide_object_id:
            out[layout.id] = None
            continue
        key = f"{pack.slides_id}:{version}:{layout.slide_object_id}"
        cached = _thumb_cache.get(key)
        if cached is None:
            try:
                cached = await client.page_thumbnail(pack.slides_id, layout.slide_object_id)
            except Exception:  # noqa: BLE001 — one bad slide must not break the tab
                cached = ""
            _thumb_cache[key] = cached
        out[layout.id] = cached or None
    return out


async def _is_stale(client: GoogleClient, pack: PackSpec) -> bool:
    """pack.json is stale once the Sheet has been edited more recently than
    the last sync — the curator's usual next move after editing ``_pack``."""
    if not pack.sheet_id or not pack.synced_at:
        return False
    try:
        meta = await client.file_meta(pack.sheet_id, fields="modifiedTime")
    except Exception:  # noqa: BLE001 — unknown beats a broken tab
        return False
    modified = str(meta.get("modifiedTime") or "")
    return bool(modified) and modified > pack.synced_at


def _load_synced_pack() -> PackSpec | None:
    return load_pack(pack_path(settings.pack_dir, settings.pack_name))


async def _pack_out(client: GoogleClient, pack: PackSpec, *, with_live_data: bool) -> PackOut:
    thumbs: dict[str, str | None] = {}
    stale = False
    if with_live_data:
        thumbs = await _thumbnails(client, pack)
        stale = await _is_stale(client, pack)
    return PackOut(
        name=pack.name,
        version=pack.version,
        synced_at=pack.synced_at,
        slides_url=deck_url(pack.slides_id) if pack.slides_id else "",
        sheet_url=sheet_edit_url(pack.sheet_id) if pack.sheet_id else "",
        folder_url=_folder_url(pack.folder_id),
        layouts=[_layout_out(layout, thumbs.get(layout.id)) for layout in pack.layouts],
        issues=list(pack.issues),
        stale=stale,
    )


async def get_pack(client: GoogleClient) -> PackOut:
    """``GET /agent/pack``. Falls back to the pack.json contents (thumbnails
    null, stale=false) when there's no synced pack or no Google credentials —
    never a 404/500 for what is, most of the time, just "nothing to show yet"."""
    pack = _load_synced_pack()
    if pack is None:
        return PackOut(
            name=settings.pack_name,
            version=0,
            synced_at="",
            issues=["no synced pack.json — run `make pack-scaffold` (or `make pack-sync`)"],
        )
    return await _pack_out(client, pack, with_live_data=credentials_present())


def _col_letter(index: int) -> str:
    """0-based column index -> spreadsheet column letters (0 -> A, 26 -> AA)."""
    letters = ""
    n = index + 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


async def update_pack_layout(
    layout_id: str, update: PackLayoutUpdate, *, client: GoogleClient
) -> PackLayoutUpdateOut:
    """``PUT /agent/pack/layouts/{id}``: write the changed cells, re-sync, and
    return the refreshed GET payload."""
    if not credentials_present():
        raise PackUnavailable("Google deck credentials are not configured — the pack is read-only.")
    pack = _load_synced_pack()
    if pack is None or not pack.sheet_id:
        raise PackUnavailable("no synced pack to edit — run `make pack-scaffold` first")

    rows = await client.read_values(pack.sheet_id, f"'{PACK_TAB}'!A1:Z200")
    if not rows:
        raise PackApiError(f"{PACK_TAB} tab is empty")
    header = [str(h).strip() for h in rows[0]]
    if "id" not in header:
        raise PackApiError(f"{PACK_TAB} tab has no `id` column")
    id_col = header.index("id")

    row_number: int | None = None
    for sheet_row, row in enumerate(rows[1:], start=2):
        padded = list(row) + [""] * max(0, len(header) - len(row))
        if padded and str(padded[id_col]).strip() == layout_id:
            row_number = sheet_row
            break
    if row_number is None:
        raise PackLayoutNotFound(f"no `_pack` row with id {layout_id!r}")

    cell_writes: list[tuple[str, Any]] = []
    if update.enabled is not None and "enabled" in header:
        cell_writes.append(("enabled", "TRUE" if update.enabled else "FALSE"))
    if update.use_when is not None and "use_when" in header:
        cell_writes.append(("use_when", update.use_when))

    for field_name, value in cell_writes:
        col = header.index(field_name)
        a1 = f"'{PACK_TAB}'!{_col_letter(col)}{row_number}"
        await client.write_values(pack.sheet_id, a1, [[value]], raw=True)

    refreshed = await sync_pack(client, name=pack.name, version=pack.version)

    # Bust the in-process catalogue caches so the very next run picks up the
    # curator's change instead of the previous sync. Imported locally: main.py
    # already imports both modules at startup, and importing sdk_agent here at
    # module scope would make this (an admin-only, occasionally-used surface)
    # part of every import of pack_api, which nothing else needs.
    from . import sdk_agent  # noqa: PLC0415

    sdk_agent._catalogue_cache.clear()
    sdk_agent._pack_catalogue_cache.clear()

    out = await _pack_out(client, refreshed, with_live_data=True)
    return PackLayoutUpdateOut(**out.model_dump(), fingerprint_changed=True)
