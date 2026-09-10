"""Template packs — parsing the snapshot and turning it into the agent's menu (s48).

The fixtures are **recorded from the real scaffolded pack** (`packs/nsw-property`
and a field-masked `presentations.get` of Pack.slides), so these tests fail if
the shape Google actually returns stops matching what the parser expects — which
is the only failure mode that matters here and the one a hand-written fixture
would hide.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent.deck import render_layouts_md
from agent.pack import LayoutSpec, PackSpec, SlotSpec, load_pack, pack_path, pack_to_catalogue

FIXTURES = Path(__file__).parent / "fixtures"
PACK_JSON = FIXTURES / "pack.json"


@pytest.fixture
def pack() -> PackSpec:
    spec = load_pack(PACK_JSON)
    assert spec is not None
    return spec


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def test_pack_json_parses_the_real_scaffolded_pack(pack: PackSpec) -> None:
    assert pack.name == "nsw-property"
    assert pack.slides_id and pack.sheet_id
    assert [layout.id for layout in pack.layouts] == ["L1", "L2", "L3", "L4", "L6", "L9"]
    assert pack.issues == ()


def test_slot_rects_are_inches_in_x_y_w_h_order(pack: PackSpec) -> None:
    layout = pack.layout("L2")
    assert layout is not None
    chart = layout.slots["chart"]
    x, y, w, h = chart.rect
    assert (round(x, 2), round(y, 2)) == (0.5, 1.25)
    assert (round(w), round(h, 1)) == (9, 3.1)
    # And it fits the page it was drawn on — a slot that runs off the edge is a
    # silently broken slide.
    assert x + w <= pack.page["width_in"] + 0.01
    assert y + h <= pack.page["height_in"] + 0.01


def test_every_enabled_layout_has_a_slide_and_a_headline(pack: PackSpec) -> None:
    for layout in pack.layouts:
        if not layout.enabled:
            continue
        assert layout.slide_object_id, layout.id
        assert "headline" in layout.slots, layout.id


def test_chart_template_resolves_to_a_real_chart_id(pack: PackSpec) -> None:
    layout = pack.layout("L2")
    assert layout is not None and layout.chart_template is not None
    assert layout.chart_template["tab"] == "tpl_series"
    assert layout.chart_template["title"] == "trend"
    assert int(layout.chart_template["chart_id"]) > 0


def test_missing_pack_is_none_not_an_error(tmp_path: Path) -> None:
    """A run without a pack must fall back to the built-in catalogue, not fail."""
    assert load_pack(tmp_path / "nope.json") is None


def test_malformed_pack_is_none_not_an_error(tmp_path: Path) -> None:
    bad = tmp_path / "pack.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_pack(bad) is None
    bad.write_text(json.dumps({"layouts": [{"id": "L1", "slots": {"headline": {"rect": [1]}}}]}))
    assert load_pack(bad) is None, "a 1-number rect is malformed, not a usable slot"


def test_pack_path_is_pack_dir_name_pack_json() -> None:
    assert pack_path("packs", "nsw-property") == Path("packs/nsw-property/pack.json")


# --------------------------------------------------------------------------
# Catalogue
# --------------------------------------------------------------------------


def test_catalogue_derives_the_legacy_rect_fields_from_slots(pack: PackSpec) -> None:
    """Everything that asks "does this layout take commentary?" must keep working."""
    catalogue = pack_to_catalogue(pack)
    l2 = next(entry for entry in catalogue if entry.id == "L2")
    assert l2.wants_chart is True
    assert l2.commentary is not None
    assert l2.table is None
    assert l2.from_pack is True
    assert l2.slide_object_id == "lib_L2"


def test_sources_slide_is_present_but_never_on_the_agents_menu(pack: PackSpec) -> None:
    """L9 is appended by the builder, so it must be reachable by id and invisible by name."""
    catalogue = pack_to_catalogue(pack)
    l9 = next(entry for entry in catalogue if entry.id == "L9")
    assert l9.enabled is False
    assert "Sources & SQL" not in render_layouts_md(catalogue)


def _layout(**over: Any) -> LayoutSpec:
    base: dict[str, Any] = {
        "id": "L2",
        "name": "Headline + Trend",
        "enabled": True,
        "use_when": "a trend",
        "slots": {"headline": SlotSpec("headline", "h", (0.5, 0.3, 9.0, 0.8))},
    }
    base.update(over)
    return LayoutSpec(**base)


def test_a_layout_with_issues_is_treated_as_disabled() -> None:
    """A half-edited pack degrades to a smaller menu, never to broken slides."""
    spec = PackSpec(
        name="p",
        version=1,
        slides_id="s",
        sheet_id="b",
        layouts=(
            _layout(),
            _layout(id="L4", name="Ranked Bars", issues=("chart_template not found",)),
        ),
    )
    catalogue = pack_to_catalogue(spec)
    assert [entry.enabled for entry in catalogue] == [True, False]
    md = render_layouts_md(catalogue)
    assert "Headline + Trend" in md
    assert "Ranked Bars" not in md


def test_layouts_md_advertises_the_slots_a_layout_accepts() -> None:
    spec = PackSpec(
        name="p",
        version=1,
        slides_id="s",
        sheet_id="b",
        layouts=(
            _layout(
                slots={
                    "headline": SlotSpec("headline", "h", (0.5, 0.3, 9.0, 0.8)),
                    "chart": SlotSpec("chart", "c", (0.5, 1.3, 9.0, 3.1)),
                    "commentary": SlotSpec("commentary", "m", (0.5, 4.5, 9.0, 0.6)),
                    # Builder-filled and marker slots must NOT be advertised: the
                    # agent passing a `footer` would be told it was dropped.
                    "footer": SlotSpec("footer", "f", (0.5, 5.1, 5.0, 0.3)),
                    "slide_id": SlotSpec("slide_id", "i", (9.5, 0.0, 0.4, 0.2)),
                }
            ),
        ),
    )
    md = render_layouts_md(pack_to_catalogue(spec))
    assert "Accepts: headline, chart, commentary" in md
    assert "footer" not in md
    assert "slide_id" not in md
