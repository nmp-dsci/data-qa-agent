"""Normalised snapshots of a generated deck and its Sheet (s48 §7).

The change log answers one question: *what did a human change after we handed
the artifact over?* Answering it needs a stable, small, comparable shape — not
the Slides API's document, which is enormous and full of ids and styling that
churn without anyone editing anything.

So both sides read through here — this module is the **one** place that turns
a raw Slides/Sheets API response into that shape. The **builder** (``deck.py``)
calls ``snapshot_deck``/``snapshot_sheet`` to take the version-1 baseline the
moment a deck is finished; ``agent.handover`` re-exports ``normalise_deck`` for
the poller (``scripts/handover_poll.py``), which fetches the same fields later
and diffs the two through ``agent.handover.diff_snapshots``. One normaliser, so
a field can never mean one thing in the baseline and another in the diff.

These functions are deliberately pure and side-effect free apart from the reads:
no database, no logging of content, no writes to the user's files — the poller
must never be able to modify what it is watching.
"""

from __future__ import annotations

from typing import Any

from .gsuite import GoogleClient

# What a slide contributes to the snapshot. Anything not listed here is
# considered styling, and restyling a deck is not an "edit" worth logging.
# ``shape.placeholder.type`` and the notesPage's ``speakerNotesObjectId`` back
# the fallback path for a hand-built master-layout pack that has no alt-text
# slots at all (§1's ``source: layout`` case).
DECK_FIELDS = (
    "slides("
    "objectId,"
    "slideProperties.notesPage("
    "notesProperties.speakerNotesObjectId,"
    "pageElements(objectId,shape.text.textElements.textRun.content)"
    "),"
    "pageElements("
    "objectId,title,"
    "shape.text.textElements.textRun.content,"
    "shape.placeholder.type,"
    # Note: linkingMode is deliberately NOT in this mask — the Slides API's
    # partial-response field expansion rejects it (400 INVALID_ARGUMENT,
    # verified live against a real deck) even though it's a documented
    # SheetsChart property; normalise_deck defaults it instead (see below).
    "sheetsChart(spreadsheetId,chartId),"
    "table(rows,columns,tableRows.tableCells.text.textElements.textRun.content))"
    ")"
)

# Fallback placeholder types when a shape carries no alt-text title at all.
_TITLE_PLACEHOLDERS = {"TITLE", "CENTER_TITLE"}
_BODY_PLACEHOLDERS = {"BODY", "SUBTITLE"}


def _shape_text(shape: dict[str, Any] | None) -> str:
    """Concatenate a shape's ``textRun`` contents, dropping the trailing
    newline every paragraph carries."""
    if not shape:
        return ""
    parts: list[str] = []
    for item in (shape.get("text") or {}).get("textElements", []):
        run = item.get("textRun") or {}
        parts.append(str(run.get("content") or ""))
    return "".join(parts).strip()


def _placeholder_type(shape: dict[str, Any] | None) -> str:
    if not shape:
        return ""
    return str((shape.get("placeholder") or {}).get("type") or "")


def _element_title(element: dict[str, Any]) -> str:
    return str(element.get("title") or "").strip().casefold()


def _notes_text(slide: dict[str, Any]) -> str:
    """Speaker notes, matched by ``speakerNotesObjectId`` rather than "the
    first non-empty text box on the notes page" — a note left blank on purpose
    must read as blank, not as whatever else happens to be on that page."""
    notes_page = (slide.get("slideProperties") or {}).get("notesPage") or {}
    notes_object_id = ((notes_page.get("notesProperties") or {}).get("speakerNotesObjectId")) or ""
    if not notes_object_id:
        return ""
    for element in notes_page.get("pageElements") or []:
        if element.get("objectId") == notes_object_id:
            return _shape_text(element.get("shape"))
    return ""


def _table_of(element: dict[str, Any]) -> dict[str, Any]:
    table = element.get("table") or {}
    cells: list[list[str]] = []
    for row in table.get("tableRows", []) or []:
        line: list[str] = []
        for cell in row.get("tableCells", []) or []:
            parts = [
                str((t.get("textRun") or {}).get("content") or "")
                for t in ((cell.get("text") or {}).get("textElements") or [])
            ]
            line.append("".join(parts).strip())
        cells.append(line)
    return {
        "object_id": str(element.get("objectId") or ""),
        "rows": int(table.get("rows") or len(table.get("tableRows") or [])),
        "cols": int(table.get("columns") or len(table.get("tableColumns") or [])),
        "cells": cells,
    }


def normalise_deck(presentation: dict[str, Any]) -> dict[str, Any]:
    """A Slides ``presentations.get`` response -> the stable snapshot shape.

    Headline/commentary come from a page element whose alt-text title is
    'headline'/'commentary' (§2); when a library slide has no such shape this
    falls back to the TITLE/BODY placeholder shapes so a snapshot is never
    silently empty. The pure half of ``snapshot_deck`` — split out so the
    differ and its tests never need a Google round trip.
    """
    slides_out: list[dict[str, Any]] = []
    for index, slide in enumerate(presentation.get("slides") or []):
        headline = ""
        commentary = ""
        title_fallback = ""
        body_fallback = ""
        texts: dict[str, str] = {}
        charts: list[dict[str, Any]] = []
        tables: list[dict[str, Any]] = []

        for el in slide.get("pageElements") or []:
            shape = el.get("shape")
            title = _element_title(el)

            if title == "headline":
                headline = _shape_text(shape)
            elif title == "commentary":
                commentary = _shape_text(shape)
            elif title and shape is not None:
                texts[title] = _shape_text(shape)

            if (
                shape is not None
                and not title_fallback
                and _placeholder_type(shape) in _TITLE_PLACEHOLDERS
            ):
                title_fallback = _shape_text(shape)
            if (
                shape is not None
                and not body_fallback
                and _placeholder_type(shape) in _BODY_PLACEHOLDERS
            ):
                body_fallback = _shape_text(shape)

            sheets_chart = el.get("sheetsChart")
            if sheets_chart is not None:
                charts.append(
                    {
                        "object_id": str(el.get("objectId") or ""),
                        "chart_id": int(sheets_chart.get("chartId") or 0),
                        "spreadsheet_id": str(sheets_chart.get("spreadsheetId") or ""),
                        "linking_mode": sheets_chart.get("linkingMode") or "NOT_LINKED_IMAGE",
                    }
                )
                continue

            table = el.get("table")
            if table is not None:
                tables.append(_table_of(el))
                continue

        slides_out.append(
            {
                "slide_object_id": str(slide.get("objectId") or ""),
                "index": index,
                "headline": headline or title_fallback,
                "commentary": commentary or body_fallback,
                "notes": _notes_text(slide),
                "charts": charts,
                "tables": tables,
                "texts": texts,
            }
        )
    return {"slides": slides_out}


async def snapshot_deck(client: GoogleClient, presentation_id: str) -> dict[str, Any]:
    """``{"slides": [{slide_object_id, index, headline, commentary, notes,
    charts, tables, texts}]}``."""
    doc = await client.get_presentation_pages(presentation_id, fields=DECK_FIELDS)
    return normalise_deck(doc)


# ---------------------------------------------------------------------------
# chart types — the sheet-side half of chart_type_changed (§7 item 3)
# ---------------------------------------------------------------------------

# Deliberately just ``spec`` (not a narrower mask): a chart's type lives under
# a different key per chart family (``basicChart``, ``pieChart``, ...) and the
# whole ChartSpec is still tiny compared to grid data.
CHART_TYPE_FIELDS = "sheets.charts(chartId,spec)"


def _chart_type(spec: dict[str, Any]) -> str:
    """The chart family's type string, normalised across ChartSpec variants.

    ``basicChart`` (line/bar/column/area/...) carries its own ``chartType``;
    every other chart family is a fixed shape, so its presence *is* the type.
    """
    basic = spec.get("basicChart")
    if basic is not None:
        return str(basic.get("chartType") or "BASIC")
    for key, label in (
        ("pieChart", "PIE"),
        ("bubbleChart", "BUBBLE"),
        ("candlestickChart", "CANDLESTICK"),
        ("orgChart", "ORG"),
        ("histogramChart", "HISTOGRAM"),
        ("scorecardChart", "SCORECARD"),
        ("waterfallChart", "WATERFALL"),
        ("treemapChart", "TREEMAP"),
    ):
        if key in spec:
            return label
    return "OTHER"


def chart_types_from_spreadsheet(doc: dict[str, Any]) -> dict[int, dict[str, str]]:
    """``{chart_id: {"chartType": ...}}`` for every chart on the spreadsheet.

    The deck snapshot only carries a chart's ``chart_id``/``spreadsheet_id``
    (a link, not its styling), so the chart's own type — the "before" a
    ``chart_type_changed`` diff needs — has to come from the Sheet, not the
    deck.
    """
    out: dict[int, dict[str, str]] = {}
    for sheet in doc.get("sheets") or []:
        for chart in sheet.get("charts") or []:
            chart_id = chart.get("chartId")
            if chart_id is None:
                continue
            out[int(chart_id)] = {"chartType": _chart_type(chart.get("spec") or {})}
    return out


async def snapshot_chart_types(
    client: GoogleClient, spreadsheet_id: str
) -> dict[int, dict[str, str]]:
    doc = await client.get_spreadsheet(spreadsheet_id, fields=CHART_TYPE_FIELDS)
    return chart_types_from_spreadsheet(doc)


async def snapshot_sheet(
    client: GoogleClient, spreadsheet_id: str, manifest: dict[str, Any]
) -> dict[str, Any]:
    """``{"tables": [{name, header, rows}], "charts": {chart_id: {chartType}}}``
    for every block the manifest names, plus every chart on the spreadsheet.

    The manifest is what says which ranges matter — a user's own scratch tab is
    not part of the handover and is deliberately not watched. ``charts`` rides
    along here (not on the deck snapshot) because the type itself is a Sheets
    property — it is what ``chart_type_changed`` diffs the deck's linked charts
    against later (§7 item 3).
    """
    tables: list[dict[str, Any]] = []
    seen: set[str] = set()
    for slide in manifest.get("slides") or []:
        name = str(slide.get("table_name") or "")
        a1 = str(slide.get("table_range") or "")
        if not name or not a1 or name in seen:
            continue
        seen.add(name)
        # A failed read is not distinguishable from a genuine deletion, and
        # this is the version-1 baseline — there is no prior snapshot to fall
        # back to, so an empty table here would bake a false deletion into the
        # trackable history forever. Left uncaught: the caller already treats
        # a failed baseline as "no baseline yet", not a fatal error.
        values = await client.read_values(spreadsheet_id, a1)
        header = [str(v) for v in (values[0] if values else [])]
        tables.append({"name": name, "header": header, "rows": [list(r) for r in values[1:]]})
    try:
        charts = await snapshot_chart_types(client, spreadsheet_id)
    except Exception:  # noqa: BLE001 — chart-type context is best-effort, never fatal
        charts = {}
    return {"tables": tables, "charts": charts}


async def snapshot_artifacts(
    client: GoogleClient,
    *,
    presentation_id: str,
    spreadsheet_id: str,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Both halves, as the artifact's ``baseline`` (§7)."""
    return {
        "deck": await snapshot_deck(client, presentation_id),
        "sheet": await snapshot_sheet(client, spreadsheet_id, manifest),
    }
