"""The handover baseline — the shape a human's edits are diffed against (s48 §7).

``built_deck.json`` is a real ``presentations.get`` of a deck this builder
produced, taken with the same field mask the snapshot uses. That matters more
than usual here: the poller compares a snapshot taken months later against this
shape, so a field that quietly changes meaning is a silent wrong answer about
whether anyone edited anything.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from agent.handover_snapshot import (
    DECK_FIELDS,
    chart_types_from_spreadsheet,
    normalise_deck,
    snapshot_artifacts,
    snapshot_chart_types,
    snapshot_deck,
    snapshot_sheet,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _built() -> dict[str, Any]:
    return json.loads((FIXTURES / "built_deck.json").read_text(encoding="utf-8"))


class FakeSnapshotClient:
    """Serves the recorded document and records what was asked for."""

    def __init__(self, values: dict[str, list[list[Any]]] | None = None) -> None:
        self.values = values or {}
        self.fields: list[str] = []
        self.ranges: list[str] = []

    async def get_presentation_pages(self, presentation_id: str, *, fields: str) -> dict[str, Any]:
        self.fields.append(fields)
        return _built()

    async def read_values(self, spreadsheet_id: str, a1_range: str) -> list[list[Any]]:
        self.ranges.append(a1_range)
        if a1_range not in self.values:
            raise RuntimeError("no such range")
        return self.values[a1_range]

    async def get_spreadsheet(
        self, spreadsheet_id: str, *, fields: str = "", include_grid_data: bool = False
    ) -> dict[str, Any]:
        return {"sheets": []}


# --------------------------------------------------------------------------
# Deck
# --------------------------------------------------------------------------


def test_snapshot_keys_every_slide_by_its_object_id() -> None:
    snap = normalise_deck(_built())
    ids = [s["slide_object_id"] for s in snap["slides"]]
    assert ids == sorted(ids), "slides come back in deck order"
    assert all(ids), "an unidentified slide cannot be diffed"


def test_snapshot_reads_headline_and_commentary_from_the_slot_alt_text() -> None:
    """The alt-text title survives duplicateObject — which is the whole reason a
    slot can be found again on a slide the builder generated."""
    slides = normalise_deck(_built())["slides"]
    assert any("rose" in s["headline"] for s in slides)
    assert any(s["commentary"] for s in slides)


def test_snapshot_records_charts_by_chart_id_and_source_spreadsheet() -> None:
    """A chart's identity is (spreadsheet, chartId): that is what tells a later
    diff "the same chart, re-styled" apart from "a different chart"."""
    charts = [c for s in normalise_deck(_built())["slides"] for c in s["charts"]]
    assert charts, "the recorded deck has linked charts"
    for chart in charts:
        assert chart["chart_id"] > 0
        assert chart["spreadsheet_id"]
        assert chart["object_id"]


def test_snapshot_records_table_contents_not_just_their_presence() -> None:
    tables = [t for s in normalise_deck(_built())["slides"] for t in s["tables"]]
    assert tables
    assert all(t["rows"] and t["cols"] for t in tables)
    assert any(t["cells"] for t in tables), "cell text is what a value edit changes"


def test_speaker_notes_are_captured() -> None:
    notes = [s["notes"] for s in normalise_deck(_built())["slides"]]
    assert any(notes), "commentary is mirrored into the notes and is editable there"


def test_snapshot_deck_asks_for_the_masked_fields_only() -> None:
    client = FakeSnapshotClient()
    snap = asyncio.run(snapshot_deck(client, "p1"))  # type: ignore[arg-type]
    assert client.fields == [DECK_FIELDS]
    assert snap["slides"]


# --------------------------------------------------------------------------
# Sheet
# --------------------------------------------------------------------------


def _manifest(**over: Any) -> dict[str, Any]:
    slide: dict[str, Any] = {"table_name": "s02_rent", "table_range": "'Data'!A2:B4"}
    slide.update(over)
    return {"slides": [slide]}


def test_sheet_snapshot_splits_header_from_rows() -> None:
    client = FakeSnapshotClient({"'Data'!A2:B4": [["m", "v"], ["2025-01", 700], ["2025-02", 707]]})
    snap = asyncio.run(snapshot_sheet(client, "s1", _manifest()))  # type: ignore[arg-type]
    assert snap["tables"] == [
        {"name": "s02_rent", "header": ["m", "v"], "rows": [["2025-01", 700], ["2025-02", 707]]}
    ]


def test_sheet_snapshot_only_reads_ranges_the_manifest_names() -> None:
    """A user's own scratch tab is not part of the handover and is not watched."""
    client = FakeSnapshotClient({"'Data'!A2:B4": [["m"]]})
    asyncio.run(snapshot_sheet(client, "s1", _manifest()))  # type: ignore[arg-type]
    assert client.ranges == ["'Data'!A2:B4"]


def test_a_deleted_range_snapshots_empty_rather_than_raising() -> None:
    """Deleting the block IS an edit; it must reach the differ, not blow up."""
    client = FakeSnapshotClient({})
    snap = asyncio.run(snapshot_sheet(client, "s1", _manifest()))  # type: ignore[arg-type]
    assert snap["tables"] == [{"name": "s02_rent", "header": [], "rows": []}]


def test_a_slide_with_no_block_contributes_no_table() -> None:
    client = FakeSnapshotClient({})
    snap = asyncio.run(
        snapshot_sheet(client, "s1", {"slides": [{"headline": "Cover"}]})  # type: ignore[arg-type]
    )
    assert snap["tables"] == []


def test_chart_types_from_spreadsheet_reads_basic_and_pie_charts() -> None:
    doc = {
        "sheets": [
            {
                "charts": [
                    {"chartId": 1, "spec": {"basicChart": {"chartType": "COLUMN"}}},
                    {"chartId": 2, "spec": {"pieChart": {}}},
                ]
            }
        ]
    }
    assert chart_types_from_spreadsheet(doc) == {
        1: {"chartType": "COLUMN"},
        2: {"chartType": "PIE"},
    }


def test_sheet_snapshot_carries_chart_types_alongside_tables() -> None:
    class ChartClient(FakeSnapshotClient):
        async def get_spreadsheet(
            self, spreadsheet_id: str, *, fields: str = "", include_grid_data: bool = False
        ) -> dict[str, Any]:
            return {
                "sheets": [
                    {"charts": [{"chartId": 9, "spec": {"basicChart": {"chartType": "LINE"}}}]}
                ]
            }

    client = ChartClient({"'Data'!A2:B4": [["m", "v"], ["2025-01", 700]]})
    snap = asyncio.run(snapshot_sheet(client, "s1", _manifest()))  # type: ignore[arg-type]
    assert snap["charts"] == {9: {"chartType": "LINE"}}


def test_snapshot_chart_types_is_best_effort_via_get_spreadsheet() -> None:
    client = FakeSnapshotClient()
    out = asyncio.run(snapshot_chart_types(client, "s1"))  # type: ignore[arg-type]
    assert out == {}


def test_artifacts_snapshot_carries_both_halves() -> None:
    client = FakeSnapshotClient({"'Data'!A2:B4": [["m", "v"], ["2025-01", 700]]})
    snap = asyncio.run(
        snapshot_artifacts(
            client,  # type: ignore[arg-type]
            presentation_id="p1",
            spreadsheet_id="s1",
            manifest=_manifest(),
        )
    )
    assert set(snap) == {"deck", "sheet"}
    assert snap["deck"]["slides"] and snap["sheet"]["tables"]
