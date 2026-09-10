"""``scripts/pack_scaffold.py --restyle`` (s48 restyle item 2).

Offline unit tests for the request-composition helpers only — no Drive/Slides/
Sheets round trip. ``pack_scaffold.py`` lives at the repo root (a curator-run
script, not an installed package), so it is imported by path the same way the
script itself locates ``agent.*``.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from pack_scaffold import (  # noqa: E402 — path bootstrap above
    ACCENT,
    BG,
    INK,
    MUTED,
    SLOT_COLOUR,
    restyle_requests,
    restyled_chart_spec,
    rgb,
)

# --------------------------------------------------------------------------
# restyle_requests — library slide repaint
# --------------------------------------------------------------------------


def _fake_doc() -> dict[str, Any]:
    """A minimal ``presentations.get`` shape: two library slides, each with a
    known slot (matched by alt-text title) and one element that isn't a slot
    at all (no title) — the kind of shape a curator's own decoration would
    leave on a library slide."""
    return {
        "slides": [
            {
                "objectId": "lib_L1",
                "pageElements": [
                    {"objectId": "L1_headline", "title": "headline"},
                    {"objectId": "L1_subtitle", "title": "subtitle"},
                    {"objectId": "L1_deco"},  # no title: not a slot, must be ignored
                ],
            },
            {
                "objectId": "lib_L3",
                "pageElements": [
                    {"objectId": "L3_kpi", "title": "kpi"},
                    {"objectId": "L3_commentary", "title": "commentary"},
                ],
            },
        ]
    }


def test_restyle_repaints_every_slides_background_to_the_light_palette() -> None:
    requests = restyle_requests(_fake_doc())
    backgrounds = [r["updatePageProperties"] for r in requests if "updatePageProperties" in r]
    assert {b["objectId"] for b in backgrounds} == {"lib_L1", "lib_L3"}
    for b in backgrounds:
        colour = b["pageProperties"]["pageBackgroundFill"]["solidFill"]["color"]["rgbColor"]
        assert colour == rgb(BG)
        assert b["fields"] == "pageBackgroundFill.solidFill.color"


def test_restyle_recolours_every_slot_by_its_alt_text_title() -> None:
    requests = restyle_requests(_fake_doc())
    styles = {
        r["updateTextStyle"]["objectId"]: r["updateTextStyle"]
        for r in requests
        if "updateTextStyle" in r
    }
    assert set(styles) == {"L1_headline", "L1_subtitle", "L3_kpi", "L3_commentary"}
    assert styles["L1_headline"]["style"]["foregroundColor"]["opaqueColor"]["rgbColor"] == rgb(INK)
    assert styles["L1_subtitle"]["style"]["foregroundColor"]["opaqueColor"]["rgbColor"] == rgb(
        MUTED
    )
    assert styles["L3_kpi"]["style"]["foregroundColor"]["opaqueColor"]["rgbColor"] == rgb(ACCENT)
    assert styles["L3_commentary"]["style"]["foregroundColor"]["opaqueColor"]["rgbColor"] == rgb(
        MUTED
    )
    for style in styles.values():
        assert style["textRange"] == {"type": "ALL"}
        assert style["fields"] == "foregroundColor"


def test_restyle_ignores_elements_with_no_slot_title() -> None:
    requests = restyle_requests(_fake_doc())
    targeted = {r["updateTextStyle"]["objectId"] for r in requests if "updateTextStyle" in r}
    assert "L1_deco" not in targeted


def test_restyle_is_a_no_op_on_a_presentation_with_no_slides() -> None:
    assert restyle_requests({"slides": []}) == []
    assert restyle_requests({}) == []


def test_slot_colour_covers_every_seed_text_slot() -> None:
    """(D5 §4) headline/commentary/kpi/kpi_label/subtitle/footer/source are the
    text slots the builder fills — a slot missing from this map would silently
    keep whatever colour it was last painted."""
    for name in ("headline", "commentary", "kpi", "kpi_label", "subtitle", "footer", "source"):
        assert name in SLOT_COLOUR


# --------------------------------------------------------------------------
# restyled_chart_spec — the template chart's background (item 2 chart half)
# --------------------------------------------------------------------------

_LIVE_CHART_SPEC = {
    "title": "trend",
    "fontName": "Inter",
    "backgroundColor": {"red": 0.1},  # deprecated twin — must be dropped
    "backgroundColorStyle": {"rgbColor": {"red": 0.1, "green": 0.1, "blue": 0.1}},
    "titleTextFormat": {"fontSize": 6},
    "dataSourceChartProperties": {"dataSourceId": "x"},
    "filterSpecs": [{"filterCriteria": {}}],
    "sortSpecs": [{"dimensionIndex": 0}],
    "basicChart": {"chartType": "LINE"},
}


def test_restyled_chart_spec_repaints_the_background_to_the_light_palette() -> None:
    spec = restyled_chart_spec(_LIVE_CHART_SPEC)
    assert spec["backgroundColorStyle"] == {"rgbColor": rgb(BG)}
    assert spec["titleTextFormat"]["foregroundColorStyle"] == {"rgbColor": rgb(BG)}
    assert spec["titleTextFormat"]["fontSize"] == 6, "the rest of titleTextFormat survives"


def test_restyled_chart_spec_drops_the_fields_the_write_side_refuses() -> None:
    spec = restyled_chart_spec(_LIVE_CHART_SPEC)
    assert "backgroundColor" not in spec
    assert "dataSourceChartProperties" not in spec
    assert "filterSpecs" not in spec
    assert "sortSpecs" not in spec
    assert spec["basicChart"] == {"chartType": "LINE"}, "chart shape is untouched"


def test_restyled_chart_spec_never_mutates_its_input() -> None:
    before = copy.deepcopy(_LIVE_CHART_SPEC)
    restyled_chart_spec(_LIVE_CHART_SPEC)
    assert _LIVE_CHART_SPEC == before
