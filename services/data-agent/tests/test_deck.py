"""Deck building — the parts that must be right before a credential exists (s46).

Everything here is offline on purpose: geometry, the layout catalogue, the
number-format mapping and the frame→rows conversion are where the real
complexity lives, and none of it should need a Google round trip to verify.
The Google calls themselves are exercised against a fake client below.
"""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any

import pandas as pd
import pytest

from agent.deck import (
    CHART_TYPES,
    DEFAULT_CATALOGUE,
    DEFAULT_TABLE_FONT_PT,
    FONT_FLOOR,
    FONT_STEPS,
    MANIFEST_COLUMNS,
    MAX_BLOCK_ROWS,
    MAX_TABLE_ROWS,
    TABLE_FONT_PT,
    DeckBuilder,
    Layout,
    Rect,
    Slot,
    _number_format,
    _rect_from_element,
    _table_requests,
    a1_column,
    a1_range,
    add_table_request,
    clone_chart_spec,
    column_type,
    fit_font_size,
    render_layouts_md,
    rows_that_fit,
    sanitize_name,
    table_row_height_in,
    update_table_columns_request,
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
# s48 restyle item 1: the cover-overflow font step-down
# --------------------------------------------------------------------------


def test_fit_font_size_keeps_the_top_size_when_short_text_already_fits() -> None:
    rect = Rect(0.5, 0.32, 9.0, 0.75)  # L2's headline slot
    assert fit_font_size("Rents rose 12%", rect, FONT_STEPS["headline"], floor=14) == 28


def test_fit_font_size_steps_down_for_the_reviewed_110_char_cover_headline() -> None:
    """The exact defect: a ~110-char headline at the scaffold's cover size
    wrapped to 4 lines, overlapping the subtitle and clipping at the edge."""
    headline = (
        "Median weekly rents across every postcode in the Northern Beaches rose "
        "steadily over the past three financial years"
    )
    assert len(headline) > 100
    cover_rect = Rect(0.7, 2.05, 8.6, 1.1)  # L1 Cover's headline slot
    size = fit_font_size(headline, cover_rect, FONT_STEPS["headline"], floor=14)
    assert size < FONT_STEPS["headline"][0], "must actually step down from the top size"
    assert size in FONT_STEPS["headline"]
    # And the chosen size must genuinely fit — the whole point of stepping down.
    chars_per_line = int((cover_rect.w * 72.0) / (0.55 * size))
    lines = -(-len(headline) // chars_per_line)
    assert lines * 1.2 * size / 72.0 <= cover_rect.h


def test_fit_font_size_floors_rather_than_shrinking_forever() -> None:
    tiny_rect = Rect(0, 0, 0.3, 0.1)
    huge_text = "x " * 500
    assert fit_font_size(huge_text, tiny_rect, FONT_STEPS["commentary"], floor=10) == 10


def test_fit_font_size_on_an_empty_slot_keeps_the_top_size() -> None:
    rect = Rect(0, 0, 9.0, 0.75)
    assert fit_font_size("", rect, FONT_STEPS["headline"], floor=14) == FONT_STEPS["headline"][0]


def test_font_floors_match_the_contract() -> None:
    assert FONT_FLOOR["headline"] == 14
    assert FONT_FLOOR["commentary"] == 10


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

    async def copy_file(self, file_id: str, name: str, **kwargs: Any) -> str:
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


# --------------------------------------------------------------------------
# s48: Sheets Tables and cloned chart specs
# --------------------------------------------------------------------------


def test_add_table_carries_no_column_properties() -> None:
    """Verified against the live API: ``columnProperties`` on the CREATE writes
    the column names one column to the right, so ``month | rent`` comes back as
    ``month | month | rent``. Typing is a separate updateTable, and this test is
    what stops someone "simplifying" the two calls back into one."""
    req = add_table_request(
        name="s02_rent", sheet_id=7, start_row=1, columns=["month", "rent"], rows=[["2026-01", 1]]
    )
    table = req["addTable"]["table"]
    assert "columnProperties" not in table
    assert table["name"] == "s02_rent"
    assert table["range"] == {
        "sheetId": 7,
        "startRowIndex": 1,
        "endRowIndex": 3,  # header + one row
        "startColumnIndex": 0,
        "endColumnIndex": 2,
    }


def test_update_table_types_columns_from_the_unit_vocabulary() -> None:
    req = update_table_columns_request(
        table_id="t1",
        columns=["month", "avg_weekly_rent", "growth_pct"],
        rows=[["2026-01", 700, 4.5]],
    )
    assert req["updateTable"]["fields"] == "columnProperties"
    types = [c["columnType"] for c in req["updateTable"]["table"]["columnProperties"]]
    assert types == ["TEXT", "CURRENCY", "PERCENT"]


def test_a_numeric_type_is_only_claimed_when_every_value_is_a_number() -> None:
    """A DOUBLE column holding "1,234 (est.)" is rejected outright by the API."""
    assert column_type("rent", [700, 710]) == "CURRENCY"
    assert column_type("rent", [700, "n/a"]) == "TEXT"
    assert column_type("rent", []) == "TEXT"
    assert column_type("flag", [True, False]) == "TEXT", "a bool is not a number"


def test_a1_range_addresses_the_block_including_its_header() -> None:
    assert a1_column(0) == "A" and a1_column(25) == "Z" and a1_column(26) == "AA"
    assert a1_range("Data", 2, 12, 2) == "'Data'!A2:B14"


def test_table_names_are_identifiers_not_labels() -> None:
    assert sanitize_name("rent trend 2077!") == "rent_trend_2077"
    assert sanitize_name("2077") == "t_2077", "a name may not start with a digit"


def test_table_rows_are_capped_by_the_slots_height() -> None:
    """A Slides table row has a minimum height: too many rows grow over the
    footer rather than shrinking to fit."""
    assert rows_that_fit(3.1) < MAX_TABLE_ROWS
    assert rows_that_fit(0.5) == 3, "never fewer than a header and a couple of rows"
    assert rows_that_fit(20.0) == MAX_TABLE_ROWS


# --------------------------------------------------------------------------
# s48 restyle item 4/5: font-size-aware table row height
# --------------------------------------------------------------------------


def test_table_row_height_matches_its_two_calibrated_anchors() -> None:
    """11pt reproduces the pre-fix 0.38in/row exactly (every existing caller and
    the assertions above depend on this); 9pt (L9's on-slide font) lands at
    0.28in/row, the value the reviewed live deck was measured against."""
    assert table_row_height_in(DEFAULT_TABLE_FONT_PT) == pytest.approx(0.38)
    assert table_row_height_in(9) == pytest.approx(0.28)


def test_rows_that_fit_holds_more_rows_at_a_smaller_font() -> None:
    assert rows_that_fit(3.7, font_pt=9) > rows_that_fit(3.7, font_pt=11)


def test_l6_and_l9_get_their_own_table_font_size() -> None:
    """(item 5) the 14pt default is what overflowed both in the reviewed deck."""
    assert TABLE_FONT_PT == {"L6": 10, "L9": 9}


def test_table_requests_set_an_explicit_font_size_on_every_cell() -> None:
    """(item 5) the 14pt Slides default is what overflowed L6 and L9; every
    cell — header included — must carry its own fontSize request, because a
    table cell's style can only be addressed by cellLocation."""
    reqs = _table_requests(
        "tbl",
        Rect(0.5, 1.25, 9.0, 3.7),
        "slide1",
        ["a", "b"],
        [[1, 2], [3, 4]],
        font_pt=9,
    )
    styles = [r["updateTextStyle"] for r in reqs if "updateTextStyle" in r]
    assert len(styles) == 2 * 3, "one style request per header + body cell"
    assert all(s["style"]["fontSize"] == {"magnitude": 9, "unit": "PT"} for s in styles)
    header_styles = [s for s in styles if s["cellLocation"]["rowIndex"] == 0]
    assert all(s["style"].get("bold") is True for s in header_styles)
    body_styles = [s for s in styles if s["cellLocation"]["rowIndex"] != 0]
    assert all("bold" not in s["style"] for s in body_styles)
    assert all(s["textRange"] == {"type": "ALL"} for s in styles)


_TEMPLATE_SPEC = {
    "title": "trend",
    "fontName": "Inter",
    "backgroundColor": {"red": 1},  # deprecated twin of backgroundColorStyle
    "backgroundColorStyle": {"rgbColor": {"red": 1}},
    "dataSourceChartProperties": {"dataSourceId": "x"},
    "basicChart": {
        "chartType": "LINE",
        "headerCount": 1,
        "legendPosition": "BOTTOM_LEGEND",
        "axis": [{"position": "BOTTOM_AXIS"}, {"position": "LEFT_AXIS"}],
        "domains": [{"domain": {"sourceRange": {"sources": [{"sheetId": 1}]}}}],
        "series": [
            {
                "series": {"sourceRange": {"sources": [{"sheetId": 1}]}},
                "targetAxis": "LEFT_AXIS",
                "colorStyle": {"rgbColor": {"blue": 1}},
            }
        ],
    },
}


def test_cloned_chart_points_at_the_new_block_and_keeps_the_packs_styling() -> None:
    spec = clone_chart_spec(
        _TEMPLATE_SPEC, sheet_id=9, start_row=17, rows=12, columns=2, series_max=3
    )
    assert spec["fontName"] == "Inter", "the pack's styling is the point of cloning"
    domain = spec["basicChart"]["domains"][0]["domain"]["sourceRange"]["sources"][0]
    assert domain == {
        "sheetId": 9,
        "startRowIndex": 17,
        "endRowIndex": 30,  # header + 12 rows
        "startColumnIndex": 0,
        "endColumnIndex": 1,
    }
    series = spec["basicChart"]["series"][0]
    assert series["series"]["sourceRange"]["sources"][0]["startColumnIndex"] == 1
    assert series["colorStyle"] == {"rgbColor": {"blue": 1}}


def test_cloned_chart_drops_the_fields_the_api_refuses_on_a_write() -> None:
    spec = clone_chart_spec(_TEMPLATE_SPEC, sheet_id=9, start_row=0, rows=3, columns=2)
    assert "dataSourceChartProperties" not in spec
    assert "backgroundColor" not in spec, "the deprecated Color twin of backgroundColorStyle"
    assert spec["backgroundColorStyle"] == {"rgbColor": {"red": 1}}
    assert spec["title"] == "", "the slide's headline is the chart's title"


def test_cloned_chart_caps_the_series_at_the_packs_limit() -> None:
    spec = clone_chart_spec(
        _TEMPLATE_SPEC, sheet_id=9, start_row=0, rows=3, columns=6, series_max=2
    )
    assert len(spec["basicChart"]["series"]) == 2
    columns = [
        s["series"]["sourceRange"]["sources"][0]["startColumnIndex"]
        for s in spec["basicChart"]["series"]
    ]
    assert columns == [1, 2]


def test_a_single_series_chart_drops_the_legend() -> None:
    """One series plus a legend saying "value" is noise on a slide."""
    spec = clone_chart_spec(_TEMPLATE_SPEC, sheet_id=9, start_row=0, rows=3, columns=2)
    assert spec["basicChart"]["legendPosition"] == "NO_LEGEND"


def test_cloning_never_mutates_the_template() -> None:
    """The spec is cached per process and reused for every slide in the deck."""
    before = json.dumps(_TEMPLATE_SPEC, sort_keys=True)
    clone_chart_spec(_TEMPLATE_SPEC, sheet_id=9, start_row=0, rows=3, columns=4)
    assert json.dumps(_TEMPLATE_SPEC, sort_keys=True) == before


# --------------------------------------------------------------------------
# s48 restyle item 3: axis unit
# --------------------------------------------------------------------------


def test_cloned_chart_labels_the_value_axis_with_a_percent_unit() -> None:
    """The defect: the yield chart's LEFT_AXIS showed bare 0/5/10/15 for a
    percent column."""
    spec = clone_chart_spec(
        _TEMPLATE_SPEC,
        sheet_id=9,
        start_row=0,
        rows=3,
        columns=2,
        column_names=["period", "gross_yield_pct"],
    )
    axes = {a["position"]: a for a in spec["basicChart"]["axis"]}
    assert axes["LEFT_AXIS"]["title"] == "gross_yield_pct (%)"
    assert "title" not in axes["BOTTOM_AXIS"]
    assert "titleTextPosition" not in axes["LEFT_AXIS"], "Sheets 500s on this field"


def test_cloned_chart_labels_a_bar_charts_bottom_value_axis() -> None:
    """A BAR chart is horizontal, so ITS value axis is BOTTOM, not LEFT."""
    bar_template = copy.deepcopy(_TEMPLATE_SPEC)
    bar_template["basicChart"]["chartType"] = "BAR"
    spec = clone_chart_spec(
        bar_template,
        sheet_id=9,
        start_row=0,
        rows=3,
        columns=2,
        column_names=["label", "avg_sale_price"],
    )
    axes = {a["position"]: a for a in spec["basicChart"]["axis"]}
    assert axes["BOTTOM_AXIS"]["title"] == "avg_sale_price ($)"
    assert "title" not in axes["LEFT_AXIS"]


def test_cloned_chart_has_no_axis_title_for_a_plain_number_column() -> None:
    """A bare "number" unit says nothing the column name doesn't already say —
    only percent and currency are unambiguous enough to add."""
    spec = clone_chart_spec(
        _TEMPLATE_SPEC, sheet_id=9, start_row=0, rows=3, columns=2, column_names=["month", "n_sold"]
    )
    axes = {a["position"]: a for a in spec["basicChart"]["axis"]}
    assert "title" not in axes["LEFT_AXIS"]


def test_cloned_chart_skips_the_axis_title_without_column_names() -> None:
    """Legacy callers that don't pass `column_names` get exactly the old spec."""
    spec = clone_chart_spec(_TEMPLATE_SPEC, sheet_id=9, start_row=0, rows=3, columns=2)
    axes = {a["position"]: a for a in spec["basicChart"]["axis"]}
    assert "title" not in axes["LEFT_AXIS"]


# --------------------------------------------------------------------------
# s48: the pack build
# --------------------------------------------------------------------------


class FakePackClient(FakeClient):
    """A FakeClient that also answers the pack build's extra calls."""

    def __init__(self) -> None:
        super().__init__()
        self.table_requests: list[dict[str, Any]] = []
        self.refuse_add_table = False
        self.written: list[tuple[str, list[list[Any]]]] = []
        self.app_properties: dict[str, dict[str, str]] = {}

    async def create_folder(self, name: str, **kwargs: Any) -> str:
        return "folder-1"

    async def find_files(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def update_file(self, file_id: str, **kwargs: Any) -> dict[str, Any]:
        props = kwargs.get("app_properties")
        if props:
            self.app_properties[file_id] = dict(props)
        return {}

    async def get_presentation_pages(self, presentation_id: str, *, fields: str) -> dict[str, Any]:
        return {
            "slides": [
                {
                    "objectId": oid,
                    "slideProperties": {
                        "notesPage": {"notesProperties": {"speakerNotesObjectId": f"{oid}_notes"}}
                    },
                }
                for oid in ("lib_L2", "lib_L9")
            ]
        }

    async def get_spreadsheet(self, spreadsheet_id: str, **kwargs: Any) -> dict[str, Any]:
        return {"sheets": [{"charts": [{"chartId": 4242, "spec": _TEMPLATE_SPEC}]}]}

    async def write_values(self, sid: str, a1: str, values: list[list[Any]], **kw: Any) -> None:
        self.values[a1] = values
        self.written.append((a1, values))

    async def sheets_batch(self, sid: str, requests: list[dict[str, Any]]) -> dict[str, Any]:
        self.sheets_batches.append(requests)
        if any("addTable" in r for r in requests):
            self.table_requests.extend(requests)
            if self.refuse_add_table:
                raise RuntimeError("addTable refused")
            return {"replies": [{"addTable": {"table": {"tableId": "tbl-1"}}}]}
        if any("addChart" in r for r in requests):
            return {"replies": [{"addChart": {"chart": {"chartId": 77}}}]}
        if any("addSheet" in r for r in requests):
            self._next_sheet_id += 1
            return {"replies": [{"addSheet": {"properties": {"sheetId": self._next_sheet_id}}}]}
        return {"replies": [{}]}


def _slot(name: str, oid: str, rect: Rect) -> Slot:
    return Slot(name=name, object_id=oid, rect=rect)


PACK_L2 = Layout(
    name="Headline + Trend",
    use_when="a trend",
    id="L2",
    source="slide",
    slide_object_id="lib_L2",
    chart=Rect(0.5, 1.25, 9.0, 3.1),
    commentary=Rect(0.5, 4.5, 9.0, 0.6),
    slots={
        "headline": _slot("headline", "L2_headline", Rect(0.5, 0.32, 9.0, 0.75)),
        "chart": _slot("chart", "L2_chart", Rect(0.5, 1.25, 9.0, 3.1)),
        "commentary": _slot("commentary", "L2_commentary", Rect(0.5, 4.5, 9.0, 0.6)),
        "footer": _slot("footer", "L2_footer", Rect(0.5, 5.12, 5.0, 0.3)),
        "source": _slot("source", "L2_source", Rect(5.6, 5.12, 3.9, 0.3)),
        "slide_id": _slot("slide_id", "L2_slide_id", Rect(9.5, 0.02, 0.4, 0.2)),
    },
    table_template="tpl_series",
    chart_template={"tab": "tpl_series", "title": "trend", "chart_id": 4242, "sheet_id": 1},
    series_max=3,
)
PACK_L9 = Layout(
    name="Sources & SQL",
    use_when="appended by the builder",
    id="L9",
    source="slide",
    slide_object_id="lib_L9",
    enabled=False,
    table=Rect(0.5, 1.25, 9.0, 3.7),
    slots={
        "headline": _slot("headline", "L9_headline", Rect(0.5, 0.32, 9.0, 0.75)),
        "table": _slot("table", "L9_table", Rect(0.5, 1.25, 9.0, 3.7)),
    },
)
PACK_CATALOGUE = (PACK_L2, PACK_L9)


def _pack_builder() -> tuple[DeckBuilder, FakePackClient]:
    client = FakePackClient()
    builder = DeckBuilder(
        client=client,  # type: ignore[arg-type]
        catalogue=PACK_CATALOGUE,
        title="T",
        run_id="run1234abcd",
        question="What is the rent trend?",
        pack_name="nsw-property",
        pack_version=1,
        pack_sheet_id="pack-sheet",
    )
    asyncio.run(builder.start("pack-slides"))
    return builder, client


def _requests(batch: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [r[kind] for r in batch if kind in r]


def test_pack_slide_duplicates_the_library_slide_and_renames_every_slot() -> None:
    """One batch, and every id in the map is deterministic: nothing later in the
    batch can target a slot whose id was assigned at random by Google."""
    builder, client = _pack_builder()
    asyncio.run(builder.add_slide(layout=PACK_L2, headline="Rents rose 12%"))
    batch = client.slides_batches[-1]
    dup = _requests(batch, "duplicateObject")[0]
    assert next(iter(batch[0])) == "duplicateObject", "the duplicate must exist first"
    assert dup["objectId"] == "lib_L2"
    assert dup["objectIds"]["lib_L2"] == "s01_slide"
    assert dup["objectIds"]["L2_headline"] == "s01_headline"
    assert dup["objectIds"]["lib_L2_notes"] == "s01_notes"


def test_pack_slide_is_moved_to_the_end_past_the_library() -> None:
    builder, client = _pack_builder()
    asyncio.run(builder.add_slide(layout=PACK_L2, headline="A"))
    asyncio.run(builder.add_slide(layout=PACK_L2, headline="B"))
    moves = [_requests(b, "updateSlidesPosition")[0] for b in client.slides_batches]
    assert [m["insertionIndex"] for m in moves] == [2, 3], "two library slides come first"
    assert moves[0]["slideObjectIds"] == ["s01_slide"]


def test_every_text_slot_is_cleared_before_it_is_filled() -> None:
    """The library slide carries sample copy. A slot the agent left empty must
    come out EMPTY, not showing the template's placeholder sentence."""
    builder, client = _pack_builder()
    asyncio.run(builder.add_slide(layout=PACK_L2, headline="Rents rose 12%"))
    batch = client.slides_batches[-1]
    cleared = {r["objectId"] for r in _requests(batch, "deleteText")}
    assert {"s01_headline", "s01_commentary", "s01_footer", "s01_source"} <= cleared
    inserted = {r["objectId"]: r["text"] for r in _requests(batch, "insertText")}
    assert inserted["s01_headline"] == "Rents rose 12%"
    assert "s01_commentary" not in inserted, "no commentary was passed"


def test_the_builder_fills_the_footer_and_the_source_line_itself() -> None:
    builder, client = _pack_builder()
    asyncio.run(builder.add_slide(layout=PACK_L2, headline="H", mart="marts.rent"))
    inserted = {
        r["objectId"]: r["text"] for r in _requests(client.slides_batches[-1], "insertText")
    }
    assert inserted["s01_footer"].startswith("Data Pilot · run1234")
    assert "marts.rent" in inserted["s01_source"]


def test_the_identity_marker_never_reaches_a_generated_slide() -> None:
    builder, client = _pack_builder()
    asyncio.run(builder.add_slide(layout=PACK_L2, headline="H"))
    deleted = {r["objectId"] for r in _requests(client.slides_batches[-1], "deleteObject")}
    assert "s01_slide_id" in deleted


def test_the_chart_placeholder_is_replaced_by_a_linked_chart_at_its_geometry() -> None:
    builder, client = _pack_builder()
    asyncio.run(
        builder.add_slide(
            layout=PACK_L2,
            headline="H",
            columns=["month", "rent"],
            rows=[["2026-01", 700], ["2026-02", 720]],
            tab_name="rent",
        )
    )
    batch = client.slides_batches[-1]
    assert "s01_chart" in {r["objectId"] for r in _requests(batch, "deleteObject")}
    chart = _requests(batch, "createSheetsChart")[0]
    assert chart["linkingMode"] == "LINKED"
    assert chart["chartId"] == 77
    props = chart["elementProperties"]
    assert props["pageObjectId"] == "s01_slide"
    assert props["transform"]["translateY"] == int(1.25 * EMU_PER_INCH)


def test_a_chart_slide_with_no_data_still_loses_its_placeholder() -> None:
    """Leaving "[chart]" written on a slide is worse than an empty region."""
    builder, client = _pack_builder()
    asyncio.run(builder.add_slide(layout=PACK_L2, headline="H"))
    batch = client.slides_batches[-1]
    assert "s01_chart" in {r["objectId"] for r in _requests(batch, "deleteObject")}
    assert not _requests(batch, "createSheetsChart")


def test_commentary_is_mirrored_into_the_speaker_notes() -> None:
    builder, client = _pack_builder()
    asyncio.run(builder.add_slide(layout=PACK_L2, headline="H", commentary="It is steady."))
    inserted = {
        r["objectId"]: r["text"] for r in _requests(client.slides_batches[-1], "insertText")
    }
    assert inserted["s01_notes"] == "It is steady."


def test_content_a_pack_layout_cannot_hold_is_reported() -> None:
    builder, _ = _pack_builder()
    record = asyncio.run(builder.add_slide(layout=PACK_L2, headline="H", kpi="$1.2m"))
    assert record.dropped == ("kpi",), "L2 has no kpi slot"


def test_the_data_block_is_named_typed_and_linked_back_to_its_slide() -> None:
    builder, client = _pack_builder()
    record = asyncio.run(
        builder.add_slide(
            layout=PACK_L2,
            headline="Rents rose",
            columns=["month", "avg_weekly_rent"],
            rows=[["2026-01", 700]],
            tab_name="rent_trend",
            query_ref="Q1",
            mart="marts.rent",
        )
    )
    assert record.table_name == "s01_rent_trend"
    assert record.table_range == "'Data'!A2:B3"
    assert record.table_kind == "table"
    assert record.query_ref == "Q1" and record.mart == "marts.rent"
    title_cell = client.values["'Data'!A1"][0][0]
    assert title_cell.startswith("=HYPERLINK(")
    assert "#slide=id.s01_slide" in title_cell
    typed = [r for b in client.sheets_batches for r in b if "updateTable" in r]
    assert typed, "the block's columns are typed after the Table is created"


def test_blocks_stack_down_the_data_tab_without_touching_each_other() -> None:
    builder, _ = _pack_builder()
    for i in range(3):
        asyncio.run(
            builder.add_slide(
                layout=PACK_L2,
                headline=f"H{i}",
                columns=["month", "rent"],
                rows=[["2026-01", 1], ["2026-02", 2]],
                tab_name=f"f{i}",
            )
        )
    ranges = [s.table_range for s in builder.slides]
    assert ranges == ["'Data'!A2:B4", "'Data'!A8:B10", "'Data'!A14:B16"]


def test_a_refused_add_table_falls_back_to_a_named_range_once() -> None:
    """Once refused it stays refused — paying the error on every slide would
    double the write cost of the whole deck."""
    builder, client = _pack_builder()
    client.refuse_add_table = True
    record = asyncio.run(
        builder.add_slide(
            layout=PACK_L2,
            headline="H",
            columns=["month", "rent"],
            rows=[["2026-01", 700]],
            tab_name="rent",
        )
    )
    assert record.table_kind == "range"
    assert any("addNamedRange" in r for batch in client.sheets_batches for r in batch)
    before = sum("addTable" in r for batch in client.sheets_batches for r in batch)
    asyncio.run(
        builder.add_slide(
            layout=PACK_L2,
            headline="H2",
            columns=["month", "rent"],
            rows=[["2026-01", 700]],
            tab_name="rent2",
        )
    )
    after = sum("addTable" in r for batch in client.sheets_batches for r in batch)
    assert after == before, "the second slide does not retry addTable"


def test_an_oversized_frame_gets_its_own_tab_and_the_data_block_points_at_it() -> None:
    builder, client = _pack_builder()
    rows = [["2026-01", i] for i in range(MAX_BLOCK_ROWS + 1)]
    record = asyncio.run(
        builder.add_slide(
            layout=PACK_L2, headline="H", columns=["month", "rent"], rows=rows, tab_name="big"
        )
    )
    assert record.table_kind == "tab"
    assert record.table_range.startswith("'s01_big'!")
    pointer = client.values["'Data'!A2"][0][0]
    assert "s01_big" in pointer


def test_pack_slide_headline_gets_an_explicit_step_down_font_size() -> None:
    """(item 1) The builder-level fix: every filled headline gets a step-down
    fontSize request right after its insertText, computed from the slide's own
    slot rect and text length — so it holds for any pack, not just the seed."""
    builder, client = _pack_builder()
    long_headline = (
        "Median weekly rents across every postcode in the Northern Beaches rose "
        "steadily over the past three financial years"
    )
    asyncio.run(builder.add_slide(layout=PACK_L2, headline=long_headline))
    batch = client.slides_batches[-1]
    order = [next(iter(r)) for r in batch]
    headline_insert_at = next(
        i
        for i, r in enumerate(batch)
        if "insertText" in r and r["insertText"]["objectId"] == "s01_headline"
    )
    assert order[headline_insert_at + 1] == "updateTextStyle", "steps down right after insertText"
    style = batch[headline_insert_at + 1]["updateTextStyle"]
    assert style["objectId"] == "s01_headline"
    assert style["style"]["fontSize"]["magnitude"] < FONT_STEPS["headline"][0]


def test_a_short_pack_headline_still_gets_an_explicit_font_size() -> None:
    """Unconditional, not overflow-triggered: a slide that already fits still
    gets a deterministic size rather than inheriting whatever the library
    slide's last curator left it at."""
    builder, client = _pack_builder()
    asyncio.run(builder.add_slide(layout=PACK_L2, headline="Rents rose 12%"))
    batch = client.slides_batches[-1]
    style = next(
        r["updateTextStyle"]
        for r in batch
        if "updateTextStyle" in r and r["updateTextStyle"]["objectId"] == "s01_headline"
    )
    assert style["style"]["fontSize"]["magnitude"] == FONT_STEPS["headline"][0]


def test_sources_slide_truncates_sql_to_80_chars_and_notes_the_manifest() -> None:
    """(item 4) Full SQL always lands in the Sheet's Manifest tab (the `rows`
    written to the Data block); the on-slide table only shows a first-line
    taste, truncated to 80 chars. L9 has no `commentary` slot in this fixture,
    so the pointer to the full SQL is appended as the table's own last row."""
    builder, client = _pack_builder()
    long_sql = "select " + "x" * 200 + " from marts.property_rent_monthly"
    asyncio.run(
        builder._append_sources(  # noqa: SLF001 — exercising the L9 fixup directly
            [{"ref": "Q1", "mart": "marts.rent", "rows": 12, "sql": long_sql}]
        )
    )
    batch = client.slides_batches[-1]
    cells = {
        (r["cellLocation"]["rowIndex"], r["cellLocation"]["columnIndex"]): r["text"]
        for r in _requests(batch, "insertText")
        if "cellLocation" in r
    }
    sql_cell = cells[(1, 3)]
    assert len(sql_cell) == 80 and sql_cell.endswith("…")
    assert cells[(2, 3)] == "Full SQL in the Sheet's Manifest tab"
    fonts = {
        (
            r["updateTextStyle"]["cellLocation"]["rowIndex"],
            r["updateTextStyle"]["cellLocation"]["columnIndex"],
        ): r["updateTextStyle"]["style"]["fontSize"]
        for r in batch
        if "updateTextStyle" in r and "cellLocation" in r["updateTextStyle"]
    }
    assert fonts[(1, 3)] == {"magnitude": 9, "unit": "PT"}, "L9's table font (item 5)"


def test_finish_appends_sources_then_clears_the_library() -> None:
    """Order is load-bearing: L9 is built by duplicating a library slide, so the
    library cannot be deleted before it exists."""
    builder, client = _pack_builder()
    asyncio.run(builder.add_slide(layout=PACK_L2, headline="H"))
    asyncio.run(
        builder.finish([{"ref": "Q1", "mart": "marts.rent", "rows": 12, "sql": "select 1"}])
    )
    layouts = [s.layout for s in builder.slides]
    assert layouts[-1] == "Sources & SQL"
    deleted = [
        r["objectId"]
        for batch in client.slides_batches
        for r in _requests(batch, "deleteObject")
        if r["objectId"].startswith("lib_")
    ]
    assert deleted == ["lib_L2", "lib_L9"]
    last_dup = [r for batch in client.slides_batches for r in _requests(batch, "duplicateObject")][
        -1
    ]
    assert last_dup["objectId"] == "lib_L9"


def test_finish_writes_a_manifest_row_per_slide() -> None:
    builder, client = _pack_builder()
    asyncio.run(
        builder.add_slide(
            layout=PACK_L2,
            headline="Rents rose",
            columns=["month", "rent"],
            rows=[["2026-01", 700]],
            tab_name="rent",
            query_ref="Q1",
            mart="marts.rent",
        )
    )
    asyncio.run(builder.finish([]))
    rows = client.values["'Manifest'!A2"]
    header = client.values["'Manifest'!A1"][0]
    assert header == list(MANIFEST_COLUMNS)
    assert len(rows[0]) == len(header), "a row that does not match the header is unreadable"
    row = dict(zip(header, rows[0], strict=True))
    assert row["layout_id"] == "L2"
    assert row["table"] == "s01_rent"
    assert row["query_ref"] == "Q1" and row["mart"] == "marts.rent"
    assert "#slide=id.s01_slide" in row["slide_url"]


def test_the_manifest_names_the_pack_that_built_the_deck() -> None:
    """A deck is only interpretable against the pack version that produced it."""
    builder, _ = _pack_builder()
    asyncio.run(builder.add_slide(layout=PACK_L2, headline="H"))
    manifest = builder.manifest()
    assert manifest["pack"] == {"name": "nsw-property", "version": 1}
    assert manifest["folder_id"] == "folder-1"
    slide = manifest["slides"][0]
    assert slide["layout_id"] == "L2"
    assert slide["slide_object_id"] == "s01_slide"
    assert slide["notes_object_id"] == "s01_notes"
    json.dumps(manifest), "the manifest crosses HTTP and lands in jsonb"


def test_generated_files_carry_the_runs_drive_metadata() -> None:
    """The handover poller finds a run's artifacts by these properties."""
    builder, client = _pack_builder()
    assert client.app_properties[builder.spreadsheet_id]["dp_run_id"] == "run1234abcd"
    assert client.app_properties[builder.spreadsheet_id]["dp_kind"] == "sheet"
    assert client.app_properties[builder.presentation_id]["dp_pack"] == "nsw-property"


def test_finish_returns_the_version_one_baseline() -> None:
    builder, _ = _pack_builder()
    asyncio.run(builder.add_slide(layout=PACK_L2, headline="H"))
    baseline = asyncio.run(builder.finish([]))
    assert set(baseline) == {"deck", "sheet"}


def test_the_library_slides_survive_until_finish() -> None:
    """They are the stock every slide is duplicated from; deleting them at start
    (the s46 behaviour) would leave the builder nothing to copy."""
    builder, client = _pack_builder()
    assert builder._library_slides == ["lib_L2", "lib_L9"]  # noqa: SLF001
    assert not any(
        r["objectId"].startswith("lib_")
        for batch in client.slides_batches
        for r in _requests(batch, "deleteObject")
    )
