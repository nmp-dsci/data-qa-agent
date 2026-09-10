#!/usr/bin/env python3
"""Build a template pack's two Google files from nothing (s48 §9, §4).

    uv run --project services/data-agent python scripts/pack_scaffold.py --name nsw-property

Creates ``Data Pilot/packs/<name>-v<n>/`` in the generating account's Drive with:

  * **Pack.slides** — one *library slide* per seed layout (L1, L2, L3, L4, L6,
    L9), built out of ordinary text boxes whose **alt-text title** names the slot
    (`headline`, `chart`, `commentary`, …). The builder duplicates one of these
    per ``add_slide``; a curator restyles them in the Slides editor with no theme
    editor and no placeholder types involved.
  * **Pack.sheet** — the ``_pack`` catalogue tab (the source of truth for which
    layouts exist and when to use them) plus ``tpl_series`` / ``tpl_ranked`` /
    ``tpl_table`` / ``tpl_sources`` tabs of sample rows. ``tpl_series`` and
    ``tpl_ranked`` each carry a hand-styled native chart whose ChartSpec the
    builder clones onto the run's real data.

Then it writes ``GOOGLE_SLIDES_TEMPLATE_ID`` / ``GOOGLE_SHEET_TEMPLATE_ID`` into
`.env` (those two keys only; no secret is ever printed) and runs ``pack_sync``.

**Idempotent.** Everything is found by Drive ``appProperties``, never by a
remembered id, so re-running reuses the folder and both files rather than
littering Drive with near-identical copies. Existing files are left alone —
delete or version-bump to rebuild, because the point of the pack is that a
curator's edits survive.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "services" / "data-agent"))

from agent.gsuite import (  # noqa: E402 — path bootstrap above
    EMU_PER_INCH,
    FOLDER_MIME,
    GoogleClient,
    credentials_present,
    deck_url,
    sheet_url,
)

# --------------------------------------------------------------------------
# Flight Deck light palette (§4)
# --------------------------------------------------------------------------

BG = "#f6f4ee"
INK = "#23293a"
ACCENT = "#9a7328"
MUTED = "#67604e"
SERIES = ("#2456c9", "#9a7328", "#35803f", "#c03535")
FONT = "Inter"  # Slides falls back to its default sans when the account lacks it

PAGE_W_IN = 10.0
PAGE_H_IN = 5.625


def rgb(hex_colour: str) -> dict[str, float]:
    h = hex_colour.lstrip("#")
    return {
        "red": int(h[0:2], 16) / 255,
        "green": int(h[2:4], 16) / 255,
        "blue": int(h[4:6], 16) / 255,
    }


def _emu(inches: float) -> int:
    return int(inches * EMU_PER_INCH)


def element_properties(page_id: str, rect: tuple[float, float, float, float]) -> dict[str, Any]:
    x, y, w, h = rect
    return {
        "pageObjectId": page_id,
        "size": {
            "width": {"magnitude": _emu(w), "unit": "EMU"},
            "height": {"magnitude": _emu(h), "unit": "EMU"},
        },
        "transform": {
            "scaleX": 1,
            "scaleY": 1,
            "translateX": _emu(x),
            "translateY": _emu(y),
            "unit": "EMU",
        },
    }


# --------------------------------------------------------------------------
# The seed catalogue (§4)
# --------------------------------------------------------------------------

# (slot, rect, sample text, font size, colour, bold, alignment)
Slot = tuple[str, tuple[float, float, float, float], str, int, str, bool, str]

_FOOTER: Slot = ("footer", (0.5, 5.12, 5.0, 0.3), "Data Pilot", 9, MUTED, False, "START")
_SOURCE: Slot = ("source", (5.6, 5.12, 3.9, 0.3), "Source: —", 9, MUTED, False, "END")
_HEADLINE: Slot = (
    "headline",
    (0.5, 0.32, 9.0, 0.75),
    "The finding, stated as a sentence",
    24,
    INK,
    True,
    "START",
)
_COMMENTARY: Slot = (
    "commentary",
    (0.5, 4.5, 9.0, 0.55),
    "What it means, in one or two sentences.",
    12,
    MUTED,
    False,
    "START",
)


def _chart_slot(rect: tuple[float, float, float, float]) -> Slot:
    # A placeholder box the builder DELETES and replaces with the real linked
    # chart at exactly this geometry — the slot is a rectangle, not a container.
    return ("chart", rect, "[chart]", 11, MUTED, False, "CENTER")


def _table_slot(rect: tuple[float, float, float, float]) -> Slot:
    return ("table", rect, "[table]", 11, MUTED, False, "CENTER")


# The colour every slot is authored in, keyed by alt-text title — the same
# lookup key `--restyle` uses to repaint an EXISTING library slide's shapes
# without knowing which layout each slide belongs to (item 2). Chart/table are
# builder-deleted placeholders, kept here only so a re-run before the builder
# has touched a slide still paints them consistently.
SLOT_COLOUR: dict[str, str] = {
    "headline": INK,
    "subtitle": MUTED,
    "footer": MUTED,
    "source": MUTED,
    "commentary": MUTED,
    "kpi": ACCENT,
    "kpi_label": MUTED,
    "chart": MUTED,
    "table": MUTED,
}


SEED_LAYOUTS: tuple[dict[str, Any], ...] = (
    {
        "id": "L1",
        "name": "Cover",
        "use_when": "The first slide of every deck: the question, restated as the answer.",
        "table_template": "",
        "chart_template": "",
        "series_max": 0,
        "grader_shape": "",
        "slots": (
            ("headline", (0.7, 2.05, 8.6, 1.1), "The answer in one line", 34, INK, True, "START"),
            (
                "subtitle",
                (0.7, 3.2, 8.6, 0.6),
                "The question this deck answers",
                14,
                MUTED,
                False,
                "START",
            ),
            _FOOTER,
        ),
    },
    {
        "id": "L2",
        "name": "Headline + Trend",
        "use_when": "One measure over time, 1-3 series. The default for a trend answer.",
        "table_template": "tpl_series",
        "chart_template": "tpl_series!trend",
        "series_max": 3,
        "grader_shape": "series",
        "slots": (_HEADLINE, _chart_slot((0.5, 1.25, 9.0, 3.1)), _COMMENTARY, _SOURCE, _FOOTER),
    },
    {
        "id": "L3",
        "name": "KPI + Trend",
        "use_when": (
            "A single headline number is the answer and a trend explains how it got there. "
            "Put the number in `kpi` (short, e.g. '$1.25m') and what it measures in `kpi_label`."
        ),
        "table_template": "tpl_series",
        "chart_template": "tpl_series!trend",
        "series_max": 3,
        "grader_shape": "scalar",
        "slots": (
            _HEADLINE,
            ("kpi", (0.5, 1.3, 2.35, 0.9), "$1.25m", 32, ACCENT, True, "START"),
            ("kpi_label", (0.5, 2.2, 2.35, 0.45), "median sale price", 10, MUTED, False, "START"),
            (
                "commentary",
                (0.5, 2.75, 2.35, 1.6),
                "What it means, in one or two sentences.",
                11,
                MUTED,
                False,
                "START",
            ),
            _chart_slot((3.15, 1.25, 6.35, 3.1)),
            _SOURCE,
            _FOOTER,
        ),
    },
    {
        "id": "L4",
        "name": "Ranked Bars",
        "use_when": "A ranking or comparison across groups — who is highest, lowest, fastest.",
        "table_template": "tpl_ranked",
        "chart_template": "tpl_ranked!ranked",
        "series_max": 1,
        "grader_shape": "ranked_set",
        "slots": (_HEADLINE, _chart_slot((0.5, 1.25, 9.0, 3.1)), _COMMENTARY, _SOURCE, _FOOTER),
    },
    {
        "id": "L6",
        "name": "Table",
        "use_when": (
            "The rows themselves are the answer — a breakdown a reader will scan or copy. "
            "Keep to about 10 rows."
        ),
        "table_template": "tpl_table",
        "chart_template": "",
        "series_max": 0,
        "grader_shape": "row_set",
        "slots": (_HEADLINE, _table_slot((0.5, 1.25, 9.0, 3.1)), _COMMENTARY, _SOURCE, _FOOTER),
    },
    {
        "id": "L9",
        "name": "Sources & SQL",
        "use_when": (
            "Appended automatically by the builder as the last slide — never choose it yourself."
        ),
        "table_template": "tpl_sources",
        "chart_template": "",
        "series_max": 0,
        "grader_shape": "",
        "enabled": False,
        "slots": (
            ("headline", (0.5, 0.32, 9.0, 0.75), "Sources & SQL", 24, INK, True, "START"),
            _table_slot((0.5, 1.25, 9.0, 3.7)),
            _FOOTER,
        ),
    },
)

PACK_HEADER = (
    "id",
    "name",
    "enabled",
    "use_when",
    "source",
    "ref",
    "table_template",
    "chart_template",
    "series_max",
    "grader_shape",
    "notes",
)

# --------------------------------------------------------------------------
# Sample data for the tpl_* tabs
# --------------------------------------------------------------------------

_MONTHS = [f"2025-{m:02d}" for m in range(1, 13)]
TPL_SERIES: list[list[Any]] = [
    ["period", "series_1", "series_2", "series_3"],
    *[[m, 700 + i * 9, 640 + i * 6, 820 + i * 12] for i, m in enumerate(_MONTHS)],
]
TPL_RANKED: list[list[Any]] = [
    ["label", "value"],
    *[[f"Group {chr(65 + i)}", 1000 - i * 63] for i in range(12)],
]
TPL_TABLE: list[list[Any]] = [
    ["label", "period", "value", "share_pct", "note"],
    *[
        [f"Group {chr(65 + i)}", _MONTHS[i], 1000 - i * 63, round(9.5 - i * 0.4, 1), ""]
        for i in range(12)
    ],
]
TPL_SOURCES: list[list[Any]] = [
    ["query_ref", "mart", "rows", "sql"],
    ["Q1", "marts.property_rent_monthly", 36, "select ... from marts.property_rent_monthly"],
]

PACK_TAB = "_pack"
TPL_TABS = ("tpl_series", "tpl_ranked", "tpl_table", "tpl_sources")
TPL_VALUES: dict[str, list[list[Any]]] = {
    "tpl_series": TPL_SERIES,
    "tpl_ranked": TPL_RANKED,
    "tpl_table": TPL_TABLE,
    "tpl_sources": TPL_SOURCES,
}
# (tab, chart title, chart type) — the title is the pack's addressing key.
TEMPLATE_CHARTS = (("tpl_series", "trend", "LINE"), ("tpl_ranked", "ranked", "BAR"))


def _sheet_id_by_title(doc: dict[str, Any], title: str) -> int | None:
    for sheet in doc.get("sheets", []):
        props = sheet.get("properties") or {}
        if str(props.get("title")) == title:
            return int(props["sheetId"])
    return None


# --------------------------------------------------------------------------
# Drive scaffolding
# --------------------------------------------------------------------------


async def _ensure_folder(
    client: GoogleClient, name: str, *, parent: str, props: dict[str, str]
) -> tuple[str, bool]:
    found = await client.find_files(app_properties=props, mime_type=FOLDER_MIME)
    if found:
        return str(found[0]["id"]), False
    return await client.create_folder(name, parent=parent, app_properties=props), True


async def _ensure_folders(client: GoogleClient, name: str, version: int) -> str:
    root, _ = await _ensure_folder(client, "Data Pilot", parent="", props={"dp_kind": "dp_root"})
    packs, _ = await _ensure_folder(
        client, "packs", parent=root, props={"dp_kind": "dp_packs_root"}
    )
    folder, fresh = await _ensure_folder(
        client,
        f"{name}-v{version}",
        parent=packs,
        props={"dp_kind": "pack_folder", "dp_pack": name, "dp_pack_version": str(version)},
    )
    print(f"{'created' if fresh else 'reusing'} folder Data Pilot/packs/{name}-v{version}")
    return folder


# --------------------------------------------------------------------------
# Pack.sheet
# --------------------------------------------------------------------------


def _styled_chart_spec(
    *,
    title: str,
    chart_type: str,
    sheet_id: int,
    rows: int,
    cols: int,
    horizontal: bool = False,
) -> dict[str, Any]:
    """The hand-styled spec the builder later clones (§4).

    Deliberately spelled out rather than left to Sheets' defaults: this spec IS
    the pack's chart design, and a curator restyling the chart in the Sheets UI
    is editing exactly this object.
    """

    def source(start_col: int, end_col: int) -> dict[str, Any]:
        return {
            "sources": [
                {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": rows + 1,
                    "startColumnIndex": start_col,
                    "endColumnIndex": end_col,
                }
            ]
        }

    # A BAR chart is horizontal, so its VALUE axis is the bottom one — Sheets
    # rejects a bar series targeting LEFT_AXIS outright ("Bar charts series may
    # only target the BOTTOM_AXIS").
    value_axis = "BOTTOM_AXIS" if chart_type == "BAR" else "LEFT_AXIS"
    series = [
        {
            "series": {"sourceRange": source(i, i + 1)},
            "targetAxis": value_axis,
            "colorStyle": {"rgbColor": rgb(SERIES[(i - 1) % len(SERIES)])},
        }
        for i in range(1, cols)
    ]
    return {
        # The title is the pack's LOOKUP KEY (`tpl_series!trend`), so the
        # template chart must carry one — drawn in the background colour at 6pt
        # so it reads as blank. The builder clears it entirely on the clone: the
        # slide's headline is the chart's title, and two titles on one slide is
        # the most common way a generated deck looks generated.
        "title": title,
        "titleTextFormat": {"foregroundColorStyle": {"rgbColor": rgb(BG)}, "fontSize": 6},
        "fontName": FONT,
        "backgroundColorStyle": {"rgbColor": rgb(BG)},
        "maximized": False,
        "basicChart": {
            "chartType": chart_type,
            "legendPosition": "BOTTOM_LEGEND" if len(series) > 1 else "NO_LEGEND",
            "headerCount": 1,
            "axis": [
                {
                    "position": "BOTTOM_AXIS",
                    # No `titleTextPosition`: Sheets answers 500 INTERNAL to an
                    # axis that carries one without a title (verified 2026-09).
                    "format": {
                        "fontFamily": FONT,
                        "fontSize": 9,
                        "foregroundColorStyle": {"rgbColor": rgb(MUTED)},
                    },
                },
                {
                    "position": "LEFT_AXIS",
                    "format": {
                        "fontFamily": FONT,
                        "fontSize": 9,
                        "foregroundColorStyle": {"rgbColor": rgb(MUTED)},
                    },
                },
            ],
            "domains": [{"domain": {"sourceRange": source(0, 1)}}],
            "series": series,
            "lineSmoothing": False,
        },
    }


async def _build_sheet_tabs(client: GoogleClient, sid: str, layouts: Any) -> None:
    """Rename the default tab to `_pack`, add the tpl_* tabs, write every value."""
    doc = await client.get_spreadsheet(sid, fields="sheets.properties")
    first = doc["sheets"][0]["properties"]
    requests: list[dict[str, Any]] = [
        {
            "updateSheetProperties": {
                "properties": {"sheetId": int(first["sheetId"]), "title": PACK_TAB},
                "fields": "title",
            }
        }
    ]
    for tab in TPL_TABS:
        requests.append({"addSheet": {"properties": {"title": tab}}})
    await client.sheets_batch(sid, requests)

    pack_rows = [list(PACK_HEADER)]
    for layout in layouts:
        pack_rows.append(
            [
                layout["id"],
                layout["name"],
                "TRUE" if layout.get("enabled", True) else "FALSE",
                layout["use_when"],
                "slide",
                layout["id"],
                layout["table_template"],
                layout["chart_template"],
                layout["series_max"],
                layout["grader_shape"],
                "",
            ]
        )
    await client.write_values(sid, f"'{PACK_TAB}'!A1", pack_rows)
    for tab, values in TPL_VALUES.items():
        await client.write_values(sid, f"'{tab}'!A1", values)

    doc = await client.get_spreadsheet(sid, fields="sheets.properties")
    await client.sheets_batch(
        sid,
        [
            {
                "repeatCell": {
                    "range": {"sheetId": _sheet_id_by_title(doc, tab), "endRowIndex": 1},
                    "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                    "fields": "userEnteredFormat.textFormat.bold",
                }
            }
            for tab in (PACK_TAB, *TPL_TABS)
        ],
    )


async def _ensure_template_charts(client: GoogleClient, sid: str) -> None:
    """Add the hand-styled template charts, skipping any that already exist.

    Idempotent by chart *title*, because the title is how `_pack` addresses a
    template (`tpl_series!trend`) — so a re-run repairs a half-built pack instead
    of stacking a second `trend` chart the sync could not disambiguate.
    """
    doc = await client.get_spreadsheet(sid, fields="sheets(properties,charts.spec.title)")
    have = {
        (str((s.get("properties") or {}).get("title")), str((c.get("spec") or {}).get("title")))
        for s in doc.get("sheets", [])
        for c in s.get("charts", []) or []
    }
    requests: list[dict[str, Any]] = []
    for tab, title, chart_type in TEMPLATE_CHARTS:
        if (tab, title) in have:
            continue
        sheet_id = _sheet_id_by_title(doc, tab)
        if sheet_id is None:
            continue
        values = TPL_VALUES[tab]
        requests.append(
            {
                "addChart": {
                    "chart": {
                        "spec": _styled_chart_spec(
                            title=title,
                            chart_type=chart_type,
                            sheet_id=sheet_id,
                            rows=len(values) - 1,
                            cols=len(values[0]),
                        ),
                        "position": {
                            "overlayPosition": {
                                "anchorCell": {
                                    "sheetId": sheet_id,
                                    "rowIndex": 1,
                                    "columnIndex": len(values[0]) + 1,
                                }
                            }
                        },
                    }
                }
            }
        )
    if requests:
        await client.sheets_batch(sid, requests)
        print(f"added {len(requests)} template chart(s)")


# --------------------------------------------------------------------------
# --restyle (item 2): repaint an EXISTING pack in place
# --------------------------------------------------------------------------

# Fields a ChartSpec carries when READ that the write side (updateChartSpec)
# rejects — the same set `agent/deck.py`'s `clone_chart_spec` drops, kept as a
# separate literal here rather than an import so this script stays runnable
# standalone (it bootstraps `sys.path` for `agent.*` only, not for tests).
_CHART_SPEC_DROP = ("dataSourceChartProperties", "filterSpecs", "sortSpecs", "backgroundColor")


def restyled_chart_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """A template chart's spec with its background repainted to the light
    palette (item 2) — cloned decks inherit this, so fixing it here fixes
    every future clone without touching the builder."""
    out = dict(spec)
    for key in _CHART_SPEC_DROP:
        out.pop(key, None)
    out["backgroundColorStyle"] = {"rgbColor": rgb(BG)}
    title_format = dict(out.get("titleTextFormat") or {})
    title_format["foregroundColorStyle"] = {"rgbColor": rgb(BG)}
    out["titleTextFormat"] = title_format
    return out


def restyle_requests(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """The Slides batch that repaints every library slide in ``doc`` (a
    ``presentations.get`` result) to the light palette, in place.

    Idempotent and non-destructive by construction: every request targets an
    ``objectId`` that already exists (the page, or a slot shape found by its
    alt-text title) — nothing is created, duplicated or deleted, so a pack.json
    already synced against this presentation's object ids stays valid.
    """
    requests: list[dict[str, Any]] = []
    for slide in doc.get("slides", []):
        page_id = str(slide.get("objectId") or "")
        if not page_id:
            continue
        requests.append(
            {
                "updatePageProperties": {
                    "objectId": page_id,
                    "pageProperties": {
                        "pageBackgroundFill": {"solidFill": {"color": {"rgbColor": rgb(BG)}}}
                    },
                    "fields": "pageBackgroundFill.solidFill.color",
                }
            }
        )
        for element in slide.get("pageElements", []):
            colour = SLOT_COLOUR.get(str(element.get("title") or ""))
            object_id = element.get("objectId")
            if colour is None or not object_id:
                continue
            requests.append(
                {
                    "updateTextStyle": {
                        "objectId": str(object_id),
                        "textRange": {"type": "ALL"},
                        "style": {"foregroundColor": {"opaqueColor": {"rgbColor": rgb(colour)}}},
                        "fields": "foregroundColor",
                    }
                }
            )
    return requests


async def restyle(client: GoogleClient, *, name: str, version: int) -> int:
    """Find an existing pack by its Drive ``appProperties`` and repaint it."""
    common = {"dp_pack": name, "dp_pack_version": str(version)}
    decks = await client.find_files(app_properties={**common, "dp_kind": "pack_slides"})
    if not decks:
        print(
            f"no Pack.slides found for {name}-v{version} — run the scaffold first", file=sys.stderr
        )
        return 2
    pid = str(decks[0]["id"])
    doc = await client.get_presentation(pid)
    requests = restyle_requests(doc)
    if requests:
        await client.slides_batch(pid, requests)
    print(f"restyled {len(doc.get('slides', []))} library slide(s) in Pack.slides")

    sheets = await client.find_files(app_properties={**common, "dp_kind": "pack_sheet"})
    if sheets:
        sid = str(sheets[0]["id"])
        chart_doc = await client.get_spreadsheet(sid, fields="sheets.charts(chartId,spec)")
        chart_requests = [
            {
                "updateChartSpec": {
                    "chartId": int(chart["chartId"]),
                    "spec": restyled_chart_spec(dict(chart.get("spec") or {})),
                }
            }
            for sheet in chart_doc.get("sheets", [])
            for chart in sheet.get("charts", []) or []
            if chart.get("chartId") is not None
        ]
        if chart_requests:
            await client.sheets_batch(sid, chart_requests)
            print(f"restyled {len(chart_requests)} template chart(s) in Pack.sheet")
    return 0


# --------------------------------------------------------------------------
# Pack.slides
# --------------------------------------------------------------------------


def slide_object_id(layout_id: str) -> str:
    """Library slides get a deterministic objectId (Slides ids need >= 5 chars).

    Slides pages have no alt text — only page *elements* do — so the slide's own
    identity is carried by its objectId, and ``pack_sync`` falls back to a
    ``slide_id`` marker element when a curator has duplicated a slide and Google
    assigned it a random id.
    """
    return f"lib_{layout_id}"


def _library_slide_requests(layout: dict[str, Any]) -> list[dict[str, Any]]:
    page = slide_object_id(layout["id"])
    reqs: list[dict[str, Any]] = [
        {"createSlide": {"objectId": page, "slideLayoutReference": {"predefinedLayout": "BLANK"}}},
        {
            "updatePageProperties": {
                "objectId": page,
                "pageProperties": {
                    "pageBackgroundFill": {"solidFill": {"color": {"rgbColor": rgb(BG)}}}
                },
                "fields": "pageBackgroundFill.solidFill.color",
            }
        },
    ]
    for slot, rect, text, size, colour, bold, align in layout["slots"]:
        oid = f"{layout['id']}_{slot}"
        reqs += [
            {
                "createShape": {
                    "objectId": oid,
                    "shapeType": "TEXT_BOX",
                    "elementProperties": element_properties(page, rect),
                }
            },
            {"insertText": {"objectId": oid, "text": text}},
            {
                "updateTextStyle": {
                    "objectId": oid,
                    "style": {
                        "fontFamily": FONT,
                        "fontSize": {"magnitude": size, "unit": "PT"},
                        "bold": bold,
                        "foregroundColor": {"opaqueColor": {"rgbColor": rgb(colour)}},
                    },
                    "fields": "fontFamily,fontSize,bold,foregroundColor",
                }
            },
            {
                "updateParagraphStyle": {
                    "objectId": oid,
                    "style": {"alignment": align},
                    "fields": "alignment",
                }
            },
            # THE contract: the alt-text title is what makes this shape a slot.
            {"updatePageElementAltText": {"objectId": oid, "title": slot}},
        ]
    # The identity marker pack_sync falls back to when a curator has duplicated
    # the slide (which loses the deterministic objectId above).
    marker = f"{layout['id']}_slide_id"
    reqs += [
        {
            "createShape": {
                "objectId": marker,
                "shapeType": "TEXT_BOX",
                "elementProperties": element_properties(page, (9.55, 0.02, 0.4, 0.2)),
            }
        },
        {"insertText": {"objectId": marker, "text": layout["id"]}},
        {
            "updateTextStyle": {
                "objectId": marker,
                "style": {
                    "fontSize": {"magnitude": 6, "unit": "PT"},
                    "foregroundColor": {"opaqueColor": {"rgbColor": rgb(BG)}},
                },
                "fields": "fontSize,foregroundColor",
            }
        },
        {"updatePageElementAltText": {"objectId": marker, "title": "slide_id"}},
    ]
    return reqs


async def _build_slides_content(client: GoogleClient, pid: str, layouts: Any) -> None:
    doc = await client.get_presentation(pid)
    requests: list[dict[str, Any]] = [
        {"deleteObject": {"objectId": str(s["objectId"])}} for s in doc.get("slides", [])
    ]
    for layout in layouts:
        requests += _library_slide_requests(layout)
    await client.slides_batch(pid, requests)


# --------------------------------------------------------------------------
# .env
# --------------------------------------------------------------------------

_ENV_KEYS = ("GOOGLE_SLIDES_TEMPLATE_ID", "GOOGLE_SHEET_TEMPLATE_ID")


def write_env(path: Path, values: dict[str, str]) -> None:
    """Append or replace exactly these keys. Never prints a value, never
    reorders or rewrites anything else in the file — `.env` holds live secrets."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = text.splitlines()
    for key, value in values.items():
        if key not in _ENV_KEYS:
            raise ValueError(f"refusing to write {key} to .env")
        pattern = re.compile(rf"^\s*#?\s*{re.escape(key)}\s*=")
        replaced = False
        for i, line in enumerate(lines):
            if pattern.match(line):
                lines[i] = f"{key}={value}"
                replaced = True
                break
        if not replaced:
            lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {', '.join(values)} to {path}")


# --------------------------------------------------------------------------


async def scaffold(name: str, version: int) -> tuple[str, str, str]:
    client = GoogleClient()
    folder = await _ensure_folders(client, name, version)
    common = {"dp_pack": name, "dp_pack_version": str(version)}

    sheets = await client.find_files(app_properties={**common, "dp_kind": "pack_sheet"})
    if sheets:
        sid = str(sheets[0]["id"])
        print("reusing Pack.sheet")
    else:
        sid, _ = await client.create_spreadsheet("Pack.sheet")
        await client.update_file(
            sid, add_parents=folder, app_properties={**common, "dp_kind": "pack_sheet"}
        )
        await _build_sheet_tabs(client, sid, SEED_LAYOUTS)
        print("created Pack.sheet")
    await _ensure_template_charts(client, sid)

    decks = await client.find_files(app_properties={**common, "dp_kind": "pack_slides"})
    if decks:
        pid = str(decks[0]["id"])
        print("reusing Pack.slides")
    else:
        pid = await client.create_presentation("Pack.slides")
        await client.update_file(
            pid, add_parents=folder, app_properties={**common, "dp_kind": "pack_slides"}
        )
        await _build_slides_content(client, pid, SEED_LAYOUTS)
        print("created Pack.slides")

    return folder, pid, sid


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="nsw-property")
    parser.add_argument("--version", type=int, default=1)
    parser.add_argument("--no-sync", action="store_true", help="skip the pack_sync run")
    parser.add_argument(
        "--restyle",
        action="store_true",
        help=(
            "repaint an EXISTING pack's library slides + template charts to the "
            "current light palette in place (updatePageProperties/updateTextStyle/"
            "updateChartSpec) — creates and deletes nothing, so object ids and any "
            "synced pack.json are unaffected and re-sync is not required"
        ),
    )
    args = parser.parse_args()

    if not credentials_present():
        print(
            "Missing GOOGLE_DECK_CLIENT_ID/_SECRET/_REFRESH_TOKEN — run "
            "`uv run python scripts/google_auth.py --write-env` first.",
            file=sys.stderr,
        )
        return 2

    if args.restyle:
        return asyncio.run(restyle(GoogleClient(), name=args.name, version=args.version))

    folder, pid, sid = asyncio.run(scaffold(args.name, args.version))
    write_env(
        REPO / ".env",
        {"GOOGLE_SLIDES_TEMPLATE_ID": pid, "GOOGLE_SHEET_TEMPLATE_ID": sid},
    )
    print(f"\nfolder: https://drive.google.com/drive/folders/{folder}")
    print(f"slides: {deck_url(pid)}")
    print(f"sheet:  {sheet_url(sid)}")

    if not args.no_sync:
        import pack_sync  # noqa: PLC0415 — sibling script, imported after the path bootstrap

        return int(pack_sync.run(name=args.name, version=args.version, check=False))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
