"""Template pack sync — the core logic behind ``scripts/pack_sync.py`` and the
Pack Inspector's ``PUT /agent/pack/layouts/{id}`` (s48 §9, §P2).

The pack lives in Google — a curator edits the slides and the ``_pack`` tab
there. ``sync_pack`` reads all of it back and writes the reviewable snapshot
the runtime loads: which layouts exist, when to use each, which library slide
it duplicates, where each slot sits (in inches), and which template chart it
clones. **Nothing else may write pack.json**: it is a snapshot, never a source
of truth, and hand-edits would silently disagree with the deck the builder
actually produces.

Validation is per layout and non-fatal: an enabled layout with an issue (no
slide with that ref, no ``headline`` slot, a ``chart_template`` that is not in
the Sheet) is recorded with its issue and treated as disabled at runtime, so a
half-edited pack degrades to a smaller menu instead of to broken slides.

This module is deliberately import-only (no argparse, no filesystem-path
guessing from ``__file__``): both the CLI (``scripts/pack_sync.py``, a thin
wrapper around ``sync_pack``) and the Pack Inspector's PUT handler
(``agent/pack_api.py``) call the exact same code, so pack.json can never drift
between "synced from the terminal" and "synced from the app".
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import settings
from .gsuite import EMU_PER_INCH, GoogleClient
from .pack import PackSpec, load_pack

PACK_TAB = "_pack"
REQUIRED_COLUMNS = ("id", "name", "enabled", "use_when", "source", "ref")
# Which alt-text titles are slots at all (§2). Anything else on a library slide
# is decoration — a rule, a logo, a curator's note — and is left alone.
SLOT_NAMES = frozenset(
    {
        "headline",
        "commentary",
        "kpi",
        "kpi_label",
        "chart",
        "table",
        "subtitle",
        "footer",
        "source",
        # Not a slot the agent fills: the identity marker the scaffold puts on
        # every library slide. Recorded so the builder can delete the copy of it
        # that duplicateObject drags onto each generated slide.
        "slide_id",
    }
)


def _rect_in(element: dict[str, Any]) -> list[float] | None:
    size = element.get("size") or {}
    transform = element.get("transform") or {}
    try:
        w = float(size["width"]["magnitude"]) * float(transform.get("scaleX", 1))
        h = float(size["height"]["magnitude"]) * float(transform.get("scaleY", 1))
        x = float(transform.get("translateX", 0))
        y = float(transform.get("translateY", 0))
    except (KeyError, TypeError, ValueError):
        return None
    return [round(v / EMU_PER_INCH, 4) for v in (x, y, w, h)]


def _rows_to_dicts(values: list[list[Any]]) -> list[dict[str, str]]:
    if not values:
        return []
    header = [str(h).strip() for h in values[0]]
    out: list[dict[str, str]] = []
    for row in values[1:]:
        if not any(str(c).strip() for c in row):
            continue
        padded = list(row) + [""] * (len(header) - len(row))
        out.append({h: str(padded[i]).strip() for i, h in enumerate(header) if h})
    return out


def _slides_by_ref(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every library slide, keyed by every name it answers to.

    A slide is addressed by its objectId (``lib_L2``, what the scaffold assigns)
    and by the text of a ``slide_id`` marker element — the fallback for a slide a
    curator duplicated in the editor, which Google gives a fresh random id.
    """
    index: dict[str, dict[str, Any]] = {}
    for slide in doc.get("slides", []):
        oid = str(slide.get("objectId") or "")
        keys = {oid}
        if oid.startswith("lib_"):
            keys.add(oid[4:])
        for element in slide.get("pageElements", []):
            if str(element.get("title") or "") != "slide_id":
                continue
            text = _element_text(element).strip()
            if text:
                keys.add(text)
        for key in keys:
            index.setdefault(key, slide)
    return index


def _element_text(element: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in ((element.get("shape") or {}).get("text") or {}).get("textElements", []):
        run = item.get("textRun") or {}
        parts.append(str(run.get("content") or ""))
    return "".join(parts)


def _slots_of(slide: dict[str, Any]) -> dict[str, dict[str, Any]]:
    slots: dict[str, dict[str, Any]] = {}
    for element in slide.get("pageElements", []):
        title = str(element.get("title") or "").strip()
        if title not in SLOT_NAMES:
            continue
        rect = _rect_in(element)
        if rect is None:
            continue
        slots[title] = {"object_id": str(element.get("objectId")), "rect": rect}
    return slots


def _charts_by_title(doc: dict[str, Any]) -> dict[tuple[str, str], dict[str, int]]:
    out: dict[tuple[str, str], dict[str, int]] = {}
    for sheet in doc.get("sheets", []):
        props = sheet.get("properties") or {}
        tab = str(props.get("title") or "")
        sheet_id = int(props.get("sheetId", 0))
        for chart in sheet.get("charts", []) or []:
            title = str((chart.get("spec") or {}).get("title") or "").strip()
            out[(tab, title)] = {
                "chart_id": int(chart.get("chartId", 0)),
                "sheet_id": sheet_id,
            }
    return out


def _layout_entry(
    row: dict[str, str],
    slide_index: dict[str, dict[str, Any]],
    charts: dict[tuple[str, str], dict[str, int]],
    tabs: dict[str, int],
) -> dict[str, Any]:
    layout_issues: list[str] = []
    source = (row.get("source") or "slide").strip() or "slide"
    ref = (row.get("ref") or row.get("id") or "").strip()
    slots: dict[str, Any] = {}
    slide_object_id = ""

    if source == "slide":
        slide = slide_index.get(ref)
        if slide is None:
            layout_issues.append(f"no library slide with ref {ref!r}")
        else:
            slide_object_id = str(slide.get("objectId"))
            slots = _slots_of(slide)
            if "headline" not in slots:
                layout_issues.append("library slide has no `headline` slot")
    elif source != "layout":
        layout_issues.append(f"unknown source {source!r}")

    chart_template: dict[str, Any] | None = None
    raw_chart = (row.get("chart_template") or "").strip()
    if raw_chart:
        tab, _, title = raw_chart.partition("!")
        found = charts.get((tab.strip(), title.strip()))
        if found is None:
            layout_issues.append(f"chart_template {raw_chart!r} not found in the Sheet")
        else:
            chart_template = {"tab": tab.strip(), "title": title.strip(), **found}

    table_template = (row.get("table_template") or "").strip()
    if table_template and table_template not in tabs:
        layout_issues.append(f"table_template tab {table_template!r} not found")

    return {
        "id": row.get("id", ""),
        "name": row.get("name", ""),
        "enabled": (row.get("enabled", "TRUE") or "").upper() != "FALSE",
        "use_when": row.get("use_when", ""),
        "source": source,
        "ref": ref,
        "slide_object_id": slide_object_id,
        "slots": slots,
        "table_template": table_template,
        "chart_template": chart_template,
        "series_max": int(row.get("series_max") or 3),
        "grader_shape": row.get("grader_shape", ""),
        "issues": layout_issues,
    }


async def _table_templates(
    client: GoogleClient,
    sheet_id: str,
    tabs: dict[str, int],
    layouts: list[dict[str, Any]],
) -> dict[str, Any]:
    """The tpl_* tabs the enabled layouts reference, with their header rows.

    The header is what the builder's typed columns are named after, so it is
    part of the contract rather than sample data: renaming ``series_1`` in the
    template renames the column in every future run Sheet.
    """
    wanted = sorted({str(e["table_template"]) for e in layouts if e.get("table_template")})
    out: dict[str, Any] = {}
    for tab in wanted:
        if tab not in tabs:
            continue
        values = await client.read_values(sheet_id, f"'{tab}'!A1:Z1")
        out[tab] = {
            "sheet_id": tabs[tab],
            "header": [str(v) for v in (values[0] if values else [])],
            "x_role": "first",
        }
    return out


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


async def build_pack_dict(client: GoogleClient, *, name: str, version: int = 1) -> dict[str, Any]:
    """Read both Google files + the ``_pack`` tab and return the pack.json body.

    Pure read — never writes anything. ``sync_pack`` below is the function that
    also persists the result; a caller that only wants to inspect (e.g. the
    CLI's ``--check``) can call this directly without touching the filesystem.
    """
    slides_id = settings.google_slides_template_id
    sheet_id = settings.google_sheet_template_id
    if not slides_id or not sheet_id:
        raise RuntimeError(
            "GOOGLE_SLIDES_TEMPLATE_ID / GOOGLE_SHEET_TEMPLATE_ID are not set — run "
            "scripts/pack_scaffold.py first."
        )

    deck = await client.get_presentation(slides_id)
    book = await client.get_spreadsheet(sheet_id, fields="sheets(properties,charts)")
    pack_rows = _rows_to_dicts(await client.read_values(sheet_id, f"'{PACK_TAB}'!A1:Z200"))
    folders = await client.find_files(
        app_properties={
            "dp_kind": "pack_folder",
            "dp_pack": name,
            "dp_pack_version": str(version),
        }
    )

    issues: list[str] = []
    if not pack_rows:
        issues.append(f"{PACK_TAB} tab is empty")
    missing_cols = [c for c in REQUIRED_COLUMNS if pack_rows and c not in pack_rows[0]]
    if missing_cols:
        issues.append(f"{PACK_TAB} is missing columns: {', '.join(missing_cols)}")

    slide_index = _slides_by_ref(deck)
    charts = _charts_by_title(book)
    tabs = {
        str((s.get("properties") or {}).get("title")): int((s["properties"])["sheetId"])
        for s in book.get("sheets", [])
    }

    layouts: list[dict[str, Any]] = [
        _layout_entry(row, slide_index, charts, tabs) for row in pack_rows
    ]

    page = deck.get("pageSize") or {}
    return {
        "name": name,
        "version": version,
        "synced_at": _now(),
        "slides_id": slides_id,
        "sheet_id": sheet_id,
        "folder_id": str(folders[0]["id"]) if folders else "",
        "page": {
            "width_in": round(
                float((page.get("width") or {}).get("magnitude", 0)) / EMU_PER_INCH, 4
            ),
            "height_in": round(
                float((page.get("height") or {}).get("magnitude", 0)) / EMU_PER_INCH, 4
            ),
        },
        "layouts": layouts,
        "table_templates": await _table_templates(client, sheet_id, tabs, layouts),
        "issues": issues,
    }


def without_timestamp(text: str) -> str:
    """``synced_at`` changes on every run, so a staleness/idempotency compare
    must ignore it — shared by the CLI's ``--check`` and anything else that
    wants to ask "did this actually change" rather than "did the clock tick"."""
    try:
        data = json.loads(text)
    except ValueError:
        return text
    data.pop("synced_at", None)
    return json.dumps(data, indent=2, sort_keys=True)


async def sync_pack(
    client: GoogleClient,
    *,
    name: str = "",
    version: int = 1,
    pack_dir: str | Path = "",
) -> PackSpec:
    """Build the pack from Google, write ``<pack_dir>/<name>/pack.json``, and
    return the parsed ``PackSpec``.

    The one function both the CLI and the Pack Inspector's PUT handler call —
    so pack.json can never drift from what either writer produces. ``pack_dir``
    defaults to ``settings.pack_dir`` (what the running service already reads
    from); the CLI passes an absolute, repo-rooted path so it works regardless
    of the caller's cwd.
    """
    name = name or settings.pack_name
    resolved_dir = Path(pack_dir) if pack_dir else Path(settings.pack_dir)
    raw = await build_pack_dict(client, name=name, version=version)
    out = resolved_dir / name / "pack.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(raw, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    spec = load_pack(out)
    if spec is None:
        raise RuntimeError(f"just-written {out} failed to parse back")
    return spec
