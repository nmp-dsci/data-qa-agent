"""Deck building — the parts that must be right before a credential exists (s46).

Everything here is offline on purpose: geometry, the layout catalogue, the
number-format mapping and the frame→rows conversion are where the real
complexity lives, and none of it should need a Google round trip to verify.
The Google calls themselves are exercised against a fake client below.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pandas as pd
import pytest

from agent.deck import (
    CHART_TYPES,
    DEFAULT_CATALOGUE,
    DeckBuilder,
    Layout,
    Rect,
    _number_format,
    _rect_from_element,
    render_layouts_md,
)
from agent.gsuite import EMU_PER_INCH
from agent.sdk_agent import _cell, _frame_table

# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------


def test_rect_converts_inches_to_emu() -> None:
    props = Rect(1.0, 2.0, 3.0, 4.0).element_properties("slide_1")
    assert props["pageObjectId"] == "slide_1"
    assert props["size"]["width"]["magnitude"] == EMU_PER_INCH * 3
    assert props["size"]["height"]["magnitude"] == EMU_PER_INCH * 4
    assert props["transform"]["translateX"] == EMU_PER_INCH * 1
    assert props["transform"]["translateY"] == EMU_PER_INCH * 2


def test_every_default_layout_fits_on_the_page() -> None:
    """A slot that runs off a 10 x 5.625in page silently produces a broken slide."""
    for layout in DEFAULT_CATALOGUE:
        for slot in (layout.chart, layout.table, layout.kpi, layout.commentary):
            if slot is None:
                continue
            assert slot.x >= 0 and slot.y >= 0, layout.name
            assert slot.x + slot.w <= 10.0, f"{layout.name} overflows width"
            assert slot.y + slot.h <= 5.625, f"{layout.name} overflows height"


def test_kpi_and_commentary_do_not_overlap() -> None:
    """They share a column in 'KPI + Chart'; stacked, never on top of each other."""
    layout = next(x for x in DEFAULT_CATALOGUE if x.name == "KPI + Chart")
    assert layout.kpi is not None and layout.commentary is not None
    assert layout.kpi.y + layout.kpi.h <= layout.commentary.y


def test_rect_from_element_applies_scale() -> None:
    rect = _rect_from_element(
        {
            "size": {
                "width": {"magnitude": EMU_PER_INCH},
                "height": {"magnitude": EMU_PER_INCH},
            },
            "transform": {"scaleX": 2, "scaleY": 3, "translateX": 0, "translateY": 0},
        }
    )
    assert rect is not None
    assert rect.w == pytest.approx(2.0)
    assert rect.h == pytest.approx(3.0)


def test_rect_from_element_rejects_malformed() -> None:
    assert _rect_from_element({"size": {}, "transform": {}}) is None


# --------------------------------------------------------------------------
# Catalogue
# --------------------------------------------------------------------------


def test_layout_reference_prefers_the_packs_own_id() -> None:
    assert Layout("A", "", predefined="TITLE_ONLY").layout_reference() == {
        "predefinedLayout": "TITLE_ONLY"
    }
    assert Layout("A", "", layout_id="p7").layout_reference() == {"layoutId": "p7"}


def test_layouts_md_lists_every_enabled_layout_with_its_slots() -> None:
    md = render_layouts_md(DEFAULT_CATALOGUE)
    for layout in DEFAULT_CATALOGUE:
        assert f"## {layout.name}" in md
    assert "chart" in md and "commentary" in md


def test_layouts_md_withholds_disabled_layouts() -> None:
    """Curation decides the agent's menu — a withheld layout must be invisible,
    not merely discouraged, or the model will still name it."""
    catalogue = (
        Layout("Shown", "use me", predefined="TITLE_ONLY"),
        Layout("Hidden", "do not use me", predefined="TITLE_ONLY", enabled=False),
    )
    md = render_layouts_md(catalogue)
    assert "Shown" in md
    assert "Hidden" not in md


def test_builder_only_resolves_enabled_layouts() -> None:
    builder = DeckBuilder(
        client=None,  # type: ignore[arg-type] — not reached by name resolution
        catalogue=(
            Layout("Shown", "", predefined="TITLE_ONLY"),
            Layout("Hidden", "", predefined="TITLE_ONLY", enabled=False),
        ),
        title="t",
    )
    assert builder.layout("Shown") is not None
    assert builder.layout("shown") is not None, "matching should be case-insensitive"
    assert builder.layout("Hidden") is None
    assert builder.layout_names() == ["Shown"]


# --------------------------------------------------------------------------
# Number formats — the app's unit vocabulary reaching Sheets
# --------------------------------------------------------------------------


def test_currency_columns_get_a_currency_format() -> None:
    fmt = _number_format("median_price")
    assert fmt is not None and fmt["type"] == "CURRENCY"


def test_percent_columns_are_not_multiplied_by_a_hundred() -> None:
    """The app stores percentage points (4.5 means 4.5%). Sheets' PERCENT type
    would render that as 450%, so percent must use a NUMBER pattern."""
    fmt = _number_format("growth_pct")
    assert fmt is not None
    assert fmt["type"] == "NUMBER"
    assert "%" in fmt["pattern"]


def test_plain_numbers_get_a_thousands_separator() -> None:
    fmt = _number_format("listings")
    assert fmt is not None and fmt["type"] == "NUMBER"


def test_money_words_win_over_count_suffixes() -> None:
    """Documents an existing quirk rather than asserting it is desirable.

    ``unit_for_column`` keys off money WORDS, so "sale_count" classifies as
    currency and a Sheet renders it as dollars. That is the same classification
    today's charts use (units.ts mirrors units.py and a parity test pins them
    together), so the deck inherits it rather than introducing it — but it is a
    real mislabel worth fixing in the shared vocabulary, not here.
    """
    fmt = _number_format("sale_count")
    assert fmt is not None and fmt["type"] == "CURRENCY"


# --------------------------------------------------------------------------
# Frame -> sheet rows
# --------------------------------------------------------------------------


def test_frame_table_preserves_requested_column_order() -> None:
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4], "c": [5, 6]})
    cols, rows = _frame_table(df, ["c", "a"])
    assert cols == ["c", "a"]
    assert rows == [[5, 1], [6, 2]]


def test_frame_table_ignores_unknown_columns() -> None:
    df = pd.DataFrame({"a": [1]})
    cols, _ = _frame_table(df, ["nope"])
    assert cols == ["a"], "an unknown column selection falls back to the whole frame"


def test_frame_table_is_row_capped() -> None:
    df = pd.DataFrame({"a": range(2000)})
    _, rows = _frame_table(df, None)
    assert len(rows) == 500


def test_cell_makes_values_json_safe() -> None:
    assert _cell(None) == ""
    assert _cell(float("nan")) == "", "NaN is not valid JSON and Sheets rejects it"
    assert _cell(pd.Timestamp("2026-01-01")) == "2026-01-01 00:00:00"
    assert _cell(1) == 1
    assert _cell("x") == "x"


def test_chart_type_vocabulary_covers_the_shapes_the_agent_emits() -> None:
    for name in ("line", "bar", "column", "area", "scatter"):
        assert name in CHART_TYPES


# --------------------------------------------------------------------------
# The builder against a fake Google
# --------------------------------------------------------------------------


class FakeClient:
    """Records calls instead of making them, so slide assembly is testable."""

    def __init__(self) -> None:
        self.slides_batches: list[list[dict[str, Any]]] = []
        self.sheets_batches: list[list[dict[str, Any]]] = []
        self.values: dict[str, list[list[Any]]] = {}
        self.shared: list[str] = []
        self._next_sheet_id = 100

    async def create_spreadsheet(self, title: str) -> tuple[str, int]:
        return "sheet-1", 0

    async def create_presentation(self, title: str) -> str:
        return "deck-1"

    async def copy_file(self, file_id: str, name: str) -> str:
        return "deck-copy"

    async def get_presentation(self, presentation_id: str) -> dict[str, Any]:
        return {"slides": []}

    async def add_sheet(self, spreadsheet_id: str, title: str) -> int:
        self._next_sheet_id += 1
        return self._next_sheet_id

    async def write_values(self, sid: str, a1: str, values: list[list[Any]]) -> None:
        self.values[a1] = values

    async def sheets_batch(self, sid: str, requests: list[dict[str, Any]]) -> dict[str, Any]:
        self.sheets_batches.append(requests)
        if any("addChart" in r for r in requests):
            return {"replies": [{"addChart": {"chart": {"chartId": 77}}}]}
        return {"replies": [{}]}

    async def slides_batch(self, pid: str, requests: list[dict[str, Any]]) -> dict[str, Any]:
        self.slides_batches.append(requests)
        return {"replies": []}

    async def share_public(self, file_id: str) -> None:
        self.shared.append(file_id)


def _builder() -> tuple[DeckBuilder, FakeClient]:
    client = FakeClient()
    builder = DeckBuilder(client=client, catalogue=DEFAULT_CATALOGUE, title="T")  # type: ignore[arg-type]
    asyncio.run(builder.start(""))
    return builder, client


def test_chart_slide_is_one_atomic_batch_with_a_native_chart() -> None:
    builder, client = _builder()
    layout = builder.layout("Title + Chart")
    assert layout is not None
    asyncio.run(
        builder.add_slide(
            layout=layout,
            headline="Rents rose 12%",
            columns=["month", "rent"],
            rows=[["2026-01", 700], ["2026-02", 720]],
            chart_type="line",
        )
    )
    batch = client.slides_batches[-1]
    kinds = [next(iter(r)) for r in batch]
    assert kinds[0] == "createSlide", "the slide must exist before anything targets it"
    assert "createSheetsChart" in kinds, "the chart must be a native Sheets chart, not an image"
    chart_req = next(r["createSheetsChart"] for r in batch if "createSheetsChart" in r)
    assert chart_req["linkingMode"] == "LINKED"
    assert chart_req["chartId"] == 77


def test_slide_title_is_filled_via_a_placeholder_mapping() -> None:
    """createSlide + insertText in the SAME batch is what makes a slide atomic."""
    builder, client = _builder()
    layout = builder.layout("Title + Chart")
    assert layout is not None
    asyncio.run(builder.add_slide(layout=layout, headline="A finding"))
    batch = client.slides_batches[-1]
    create = next(r["createSlide"] for r in batch if "createSlide" in r)
    mapped = create["placeholderIdMappings"][0]["objectId"]
    inserts = [r["insertText"] for r in batch if "insertText" in r]
    assert any(i["objectId"] == mapped and i["text"] == "A finding" for i in inserts)


def test_commentary_and_kpi_land_only_where_the_layout_has_a_slot() -> None:
    builder, client = _builder()
    plain = builder.layout("Title + Chart")
    assert plain is not None and plain.kpi is None
    asyncio.run(builder.add_slide(layout=plain, headline="H", kpi="$1.2m", commentary="note"))
    kinds = [next(iter(r)) for r in client.slides_batches[-1]]
    assert "createShape" not in kinds, "a layout without slots must not gain stray text boxes"

    kpi_layout = builder.layout("KPI + Chart")
    assert kpi_layout is not None
    asyncio.run(builder.add_slide(layout=kpi_layout, headline="H", kpi="$1.2m", commentary="note"))
    texts = [r["insertText"]["text"] for r in client.slides_batches[-1] if "insertText" in r]
    assert "$1.2m" in texts and "note" in texts


def test_content_a_layout_cannot_hold_is_reported_not_silently_dropped() -> None:
    """A slide that quietly eats the model's commentary looks like a bug in the deck.

    The drop itself is correct — a layout is a fixed set of rectangles. What
    matters is that the record says so, because that is what lets the tool result
    tell the model to re-add the slide on a layout that fits.
    """
    builder, _ = _builder()
    plain = builder.layout("Title + Chart")
    assert plain is not None and plain.commentary is None and plain.kpi is None
    record = asyncio.run(
        builder.add_slide(layout=plain, headline="H", kpi="$1.2m", commentary="note")
    )
    assert set(record.dropped) == {"commentary", "kpi"}

    fits = builder.layout("KPI + Chart")
    assert fits is not None
    kept = asyncio.run(builder.add_slide(layout=fits, headline="H", kpi="$1.2m", commentary="note"))
    assert kept.dropped == (), "a layout with the slots must report nothing dropped"


def test_nothing_is_reported_dropped_when_nothing_was_passed() -> None:
    builder, _ = _builder()
    layout = builder.layout("Title + Chart")
    assert layout is not None
    record = asyncio.run(builder.add_slide(layout=layout, headline="H"))
    assert record.dropped == ()


def test_manifest_records_the_template_picked_and_every_option_passed() -> None:
    """The manifest must be enough to explain a slide without opening the deck.

    This is the read-back surface: which layout the agent chose, from which
    menu, and with what. Asserting the numbers is G1's job and asserting the
    shape is G5's; this is the third thing, which is being able to answer "why
    does this slide look like that?" months later.
    """
    builder, _ = _builder()
    layout = builder.layout("Chart + Commentary")
    assert layout is not None
    asyncio.run(
        builder.add_slide(
            layout=layout,
            headline="Rents rose 12%",
            commentary="Driven by 2-bedroom units.",
            columns=["month", "rent"],
            rows=[["2026-01", 700]],
            chart_type="line",
            tab_name="rent_trend",
        )
    )
    manifest = builder.manifest()
    assert manifest["layouts_used"] == ["Chart + Commentary"]
    assert manifest["template_pack"] == "built-in catalogue"
    assert manifest["catalogue"] == builder.layout_names(), (
        "a deck is only interpretable against the menu that was live when it was built"
    )
    spec = manifest["slides"][0]["spec"]
    assert spec["layout"] == "Chart + Commentary"
    assert spec["commentary"] == "Driven by 2-bedroom units."
    assert spec["columns"] == ["month", "rent"]
    assert spec["chart_type"] == "line"
    assert spec["rendered_as"] == "chart"
    assert spec["frame"] == "rent_trend"
    assert spec["rows"] == 1


def test_manifest_spec_is_json_serialisable() -> None:
    """It crosses an HTTP boundary and lands in jsonb — tuples would not survive."""
    builder, _ = _builder()
    layout = builder.layout("Title + Chart")
    assert layout is not None
    asyncio.run(builder.add_slide(layout=layout, headline="H", commentary="dropped"))
    json.dumps(builder.manifest())


def test_header_row_is_written_and_bolded() -> None:
    builder, client = _builder()
    layout = builder.layout("Title + Chart")
    assert layout is not None
    asyncio.run(
        builder.add_slide(
            layout=layout, headline="H", columns=["month", "rent"], rows=[["2026-01", 700]]
        )
    )
    written = next(iter(client.values.values()))
    assert written[0] == ["month", "rent"], "the header row drives the chart's series names"
    assert any(
        "repeatCell" in r and r["repeatCell"]["cell"]["userEnteredFormat"].get("textFormat")
        for batch in client.sheets_batches
        for r in batch
    )


def test_manifest_records_content_shape_not_layout_correctness() -> None:
    """The graders diff this. It records what a slide contains — never an
    assertion that a particular layout was chosen, which the agent decides."""
    builder, _ = _builder()
    layout = builder.layout("Title + Chart")
    assert layout is not None
    asyncio.run(
        builder.add_slide(layout=layout, headline="H", columns=["m", "v"], rows=[["2026-01", 1]])
    )
    manifest = builder.manifest()
    assert manifest["deck_url"].endswith("/edit")
    assert manifest["embed_url"].endswith("/embed")
    assert manifest["sheet_url"].startswith("https://docs.google.com/spreadsheets/")
    slide = manifest["slides"][0]
    assert slide["has_chart"] is True
    assert slide["rows"] == 1


def test_tab_names_stay_unique() -> None:
    builder, client = _builder()
    layout = builder.layout("Title + Chart")
    assert layout is not None
    for _ in range(2):
        asyncio.run(
            builder.add_slide(
                layout=layout, headline="H", columns=["m"], rows=[["x"]], tab_name="df"
            )
        )
    assert len({s.sheet_tab for s in builder.slides}) == 2


def test_publish_shares_both_artifacts() -> None:
    """A deck the visitor can see but whose data they cannot copy defeats the
    point — both files are shared, or neither is."""
    builder, client = _builder()
    asyncio.run(builder.publish())
    assert set(client.shared) == {"deck-1", "sheet-1"}
