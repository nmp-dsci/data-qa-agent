"""normalise_deck / normalise_sheet / diff_snapshots — offline, no Google or DB
credential needed (s48 §6/§7). These are the functions the poller's whole
correctness rests on, so they're exercised directly against small fixture
JSON shaped like real Slides/Sheets API responses."""

from __future__ import annotations

from agent.handover import diff_snapshots, normalise_deck, normalise_sheet


def _text_shape(title: str, text: str, *, placeholder_type: str | None = None) -> dict:
    shape: dict = {"text": {"textElements": [{"textRun": {"content": text + "\n"}}]}}
    if placeholder_type:
        shape["placeholder"] = {"type": placeholder_type}
    element: dict = {"objectId": f"el_{title}", "shape": shape}
    if title:
        element["title"] = title
    return element


def _presentation(slides: list[dict]) -> dict:
    return {"slides": slides}


def _slide(object_id: str, elements: list[dict], notes: str | None = None) -> dict:
    slide = {"objectId": object_id, "pageElements": elements}
    if notes is not None:
        notes_shape_id = f"{object_id}_notes"
        slide["slideProperties"] = {
            "notesPage": {
                "notesProperties": {"speakerNotesObjectId": notes_shape_id},
                "pageElements": [
                    {
                        "objectId": notes_shape_id,
                        "shape": {"text": {"textElements": [{"textRun": {"content": notes}}]}},
                    }
                ],
            }
        }
    return slide


# ---------------------------------------------------------------------------
# normalise_deck
# ---------------------------------------------------------------------------


def test_normalise_deck_reads_alt_text_slots() -> None:
    pres = _presentation(
        [
            _slide(
                "s1",
                [
                    _text_shape("headline", "Rent trend in 2077"),
                    _text_shape("commentary", "Up 4% since Q1"),
                ],
                notes="ask the analyst about outliers",
            )
        ]
    )
    out = normalise_deck(pres)
    assert out["slides"] == [
        {
            "slide_object_id": "s1",
            "index": 0,
            "headline": "Rent trend in 2077",
            "commentary": "Up 4% since Q1",
            "notes": "ask the analyst about outliers",
            "charts": [],
            "tables": [],
            "texts": {},
        }
    ]


def test_normalise_deck_falls_back_to_title_body_placeholders() -> None:
    """A hand-built master-layout pack (source: layout, §1) has no alt-text
    slots at all — the snapshot must not come back empty."""
    pres = _presentation(
        [
            _slide(
                "s1",
                [
                    _text_shape("", "Median sale price", placeholder_type="TITLE"),
                    _text_shape("", "Hornsby leads", placeholder_type="BODY"),
                ],
            )
        ]
    )
    out = normalise_deck(pres)
    assert out["slides"][0]["headline"] == "Median sale price"
    assert out["slides"][0]["commentary"] == "Hornsby leads"


def test_normalise_deck_captures_charts_and_tables() -> None:
    pres = _presentation(
        [
            {
                "objectId": "s1",
                "pageElements": [
                    {
                        "objectId": "chart1",
                        "title": "chart",
                        "sheetsChart": {
                            "chartId": 555,
                            "spreadsheetId": "sp1",
                            "linkingMode": "LINKED",
                        },
                    },
                    {
                        "objectId": "table1",
                        "title": "table",
                        "table": {"rows": 4, "columns": 3},
                    },
                ],
            }
        ]
    )
    out = normalise_deck(pres)
    slide = out["slides"][0]
    assert slide["charts"] == [
        {"object_id": "chart1", "chart_id": 555, "spreadsheet_id": "sp1", "linking_mode": "LINKED"}
    ]
    assert slide["tables"] == [{"object_id": "table1", "rows": 4, "cols": 3, "cells": []}]


# ---------------------------------------------------------------------------
# normalise_sheet
# ---------------------------------------------------------------------------


def test_normalise_sheet_slices_by_table_range() -> None:
    values = {
        "Data": [
            ["#1 · Headline + Trend"],
            ["period", "rent"],
            ["2024-01", 500],
            ["2024-02", 510],
            [],
            [],
        ]
    }
    manifest = {
        "slides": [
            {
                "slide_object_id": "s1",
                "table_name": "s01_trend",
                "table_range": "Data!A2:B4",
                "layout_id": "L2",
            }
        ]
    }
    out = normalise_sheet(values, manifest)
    assert out == {
        "tables": [
            {
                "name": "s01_trend",
                "header": ["period", "rent"],
                "rows": [["2024-01", 500], ["2024-02", 510]],
            }
        ]
    }


def test_normalise_sheet_skips_slides_without_a_table() -> None:
    manifest = {"slides": [{"slide_object_id": "s1", "table_name": None, "table_range": None}]}
    assert normalise_sheet({}, manifest) == {"tables": []}


# ---------------------------------------------------------------------------
# diff_snapshots — deck
# ---------------------------------------------------------------------------


def _deck_manifest() -> dict:
    return {
        "slides": [
            {"slide_object_id": "s1", "layout_id": "L2", "index": 0, "table_name": "s01_trend"},
        ]
    }


def test_diff_deck_detects_headline_and_commentary_changes() -> None:
    before = {
        "slides": [
            {
                "slide_object_id": "s1",
                "index": 0,
                "headline": "Old headline",
                "commentary": "Old commentary",
                "notes": "",
                "charts": [],
                "tables": [],
                "texts": {},
            }
        ]
    }
    after = {
        "slides": [
            {
                "slide_object_id": "s1",
                "index": 0,
                "headline": "New headline",
                "commentary": "Old commentary",
                "notes": "",
                "charts": [],
                "tables": [],
                "texts": {},
            }
        ]
    }
    events = diff_snapshots(before, after, _deck_manifest())
    assert events == [
        {
            "event": "headline_changed",
            "layout_id": "L2",
            "slide_index": 0,
            "slide_object_id": "s1",
            "table_name": None,
            "before": "Old headline",
            "after": "New headline",
        }
    ]


def test_diff_deck_detects_slide_added_deleted_and_reordered() -> None:
    before = {
        "slides": [
            {
                "slide_object_id": "s1",
                "index": 0,
                "headline": "A",
                "commentary": "",
                "notes": "",
                "charts": [],
                "tables": [],
                "texts": {},
            },
            {
                "slide_object_id": "s2",
                "index": 1,
                "headline": "B",
                "commentary": "",
                "notes": "",
                "charts": [],
                "tables": [],
                "texts": {},
            },
        ]
    }
    after = {
        "slides": [
            {
                "slide_object_id": "s2",
                "index": 0,
                "headline": "B",
                "commentary": "",
                "notes": "",
                "charts": [],
                "tables": [],
                "texts": {},
            },
            {
                "slide_object_id": "s3",
                "index": 1,
                "headline": "C",
                "commentary": "",
                "notes": "",
                "charts": [],
                "tables": [],
                "texts": {},
            },
        ]
    }
    events = diff_snapshots(before, after, {"slides": []})
    kinds = {e["event"] for e in events}
    assert kinds == {"slide_reordered", "slide_added", "slide_deleted"}
    reordered = next(e for e in events if e["event"] == "slide_reordered")
    assert reordered["slide_object_id"] == "s2"
    assert reordered["before"] == 1 and reordered["after"] == 0
    added = next(e for e in events if e["event"] == "slide_added")
    assert added["slide_object_id"] == "s3"
    deleted = next(e for e in events if e["event"] == "slide_deleted")
    assert deleted["slide_object_id"] == "s1"


def test_diff_deck_detects_chart_added_deleted_and_type_changed() -> None:
    def slide(charts: list[dict]) -> dict:
        return {
            "slide_object_id": "s1",
            "index": 0,
            "headline": "H",
            "commentary": "",
            "notes": "",
            "charts": charts,
            "tables": [],
            "texts": {},
        }

    before = {
        "slides": [
            slide(
                [
                    {
                        "object_id": "c1",
                        "chart_id": 1,
                        "spreadsheet_id": "sp",
                        "linking_mode": "LINKED",
                    }
                ]
            )
        ]
    }
    after = {
        "slides": [
            slide(
                [
                    {
                        "object_id": "c1",
                        "chart_id": 1,
                        "spreadsheet_id": "sp",
                        "linking_mode": "LINKED",
                    },
                    {
                        "object_id": "c2",
                        "chart_id": 2,
                        "spreadsheet_id": "sp",
                        "linking_mode": "LINKED",
                    },
                ]
            )
        ]
    }
    events = diff_snapshots(before, after, _deck_manifest())
    assert any(e["event"] == "chart_added" for e in events)

    before2 = {
        "slides": [
            slide(
                [
                    {
                        "object_id": "c1",
                        "chart_id": 1,
                        "spreadsheet_id": "sp",
                        "linking_mode": "LINKED",
                    }
                ]
            )
        ]
    }
    after2 = {"slides": [slide([])]}
    events2 = diff_snapshots(before2, after2, _deck_manifest())
    assert any(e["event"] == "chart_deleted" for e in events2)

    before3 = {
        "slides": [
            slide(
                [
                    {
                        "object_id": "c1",
                        "chart_id": 1,
                        "spreadsheet_id": "sp",
                        "linking_mode": "LINKED",
                    }
                ]
            )
        ]
    }
    after3 = {
        "slides": [
            slide(
                [
                    {
                        "object_id": "c1",
                        "chart_id": 1,
                        "spreadsheet_id": "sp",
                        "linking_mode": "LINKED",
                    }
                ]
            )
        ]
    }
    events3 = diff_snapshots(
        before3,
        after3,
        _deck_manifest(),
        charts_before={1: {"chartType": "LINE"}},
        charts_after={1: {"chartType": "COLUMN"}},
    )
    type_changed = next(e for e in events3 if e["event"] == "chart_type_changed")
    assert type_changed["before"] == "LINE" and type_changed["after"] == "COLUMN"


def test_diff_deck_without_chart_specs_does_not_emit_type_changed() -> None:
    """charts_before/charts_after are optional — no Sheet chart-spec round trip
    means the caller simply can't tell type changes, not a false positive."""
    slide = {
        "slide_object_id": "s1",
        "index": 0,
        "headline": "H",
        "commentary": "",
        "notes": "",
        "charts": [
            {"object_id": "c1", "chart_id": 1, "spreadsheet_id": "sp", "linking_mode": "LINKED"}
        ],
        "tables": [],
        "texts": {},
    }
    events = diff_snapshots({"slides": [slide]}, {"slides": [slide]}, _deck_manifest())
    assert events == []


# ---------------------------------------------------------------------------
# diff_snapshots — sheet
# ---------------------------------------------------------------------------


def _sheet_manifest() -> dict:
    return {
        "slides": [
            {"slide_object_id": "s1", "index": 0, "layout_id": "L6", "table_name": "s01_rows"}
        ]
    }


def test_diff_sheet_detects_rows_added_and_values_changed() -> None:
    before = {
        "tables": [{"name": "s01_rows", "header": ["suburb", "rent"], "rows": [["Hornsby", 500]]}]
    }
    after_added = {
        "tables": [
            {
                "name": "s01_rows",
                "header": ["suburb", "rent"],
                "rows": [["Hornsby", 500], ["Normanhurst", 480]],
            }
        ]
    }
    events = diff_snapshots(before, after_added, _sheet_manifest())
    added = next(e for e in events if e["event"] == "table_rows_added")
    assert added["table_name"] == "s01_rows"
    assert added["layout_id"] == "L6"

    after_changed = {
        "tables": [{"name": "s01_rows", "header": ["suburb", "rent"], "rows": [["Hornsby", 505]]}]
    }
    events2 = diff_snapshots(before, after_changed, _sheet_manifest())
    assert events2[0]["event"] == "table_values_changed"
    assert events2[0]["before"]["rows"] == [["Hornsby", 500]]
    assert events2[0]["after"]["rows"] == [["Hornsby", 505]]


def test_diff_sheet_no_change_emits_nothing() -> None:
    table = {"name": "s01_rows", "header": ["suburb", "rent"], "rows": [["Hornsby", 500]]}
    events = diff_snapshots({"tables": [table]}, {"tables": [table]}, _sheet_manifest())
    assert events == []
