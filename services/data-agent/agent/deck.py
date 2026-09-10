"""Deck building: the layout catalogue and the Sheets+Slides writer (s46).

The agent names a layout from a curated catalogue and supplies the content; this
module turns that into one atomic Slides ``batchUpdate`` per slide, backed by a
Google Sheet that holds the rows and owns every native chart.

Why a Sheet is not optional: the Slides API has **no primitive that authors a
chart from data**. Its only native chart object is ``createSheetsChart``, which
embeds a chart that already exists in a spreadsheet. Anything else is a picture.
So every chart is born in the Sheet and embedded into the slide.

The catalogue has two sources:

  * **A curated template pack** (``GOOGLE_SLIDES_TEMPLATE_ID``) — a hand-built
    presentation whose master carries named layouts. Read once via
    ``presentations.get``; ``layoutProperties.displayName`` is the name the agent
    uses. This is the lift-and-shift seam: a new use case swaps the pack.
  * **A built-in default** — predefined Slides layouts plus explicit geometry, so
    the feature works before anyone has built a pack.

Either way the catalogue is rendered into the run workspace as ``layouts.md``,
which the agent Greps before choosing — the same motion it already uses for
knowledge pages.
"""

from __future__ import annotations

import contextlib
import copy
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .gsuite import EMU_PER_INCH, FOLDER_MIME, GoogleClient, deck_embed_url, deck_url, sheet_url
from .handover_snapshot import snapshot_artifacts
from .units import CURRENCY, NUMBER, PERCENT, unit_for_column


def _in(value: float) -> int:
    return int(value * EMU_PER_INCH)


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    def element_properties(self, page_id: str) -> dict[str, Any]:
        return {
            "pageObjectId": page_id,
            "size": {
                "width": {"magnitude": _in(self.w), "unit": "EMU"},
                "height": {"magnitude": _in(self.h), "unit": "EMU"},
            },
            "transform": {
                "scaleX": 1,
                "scaleY": 1,
                "translateX": _in(self.x),
                "translateY": _in(self.y),
                "unit": "EMU",
            },
        }


@dataclass(frozen=True)
class Slot:
    """One fillable region of a library slide (s48 §2).

    A slot is an ordinary shape whose alt-text *title* names it, so a curator
    creates one in the Slides editor with no theme editor and no placeholder
    types — which is the whole reason library slides beat master layouts here.
    """

    name: str
    object_id: str
    rect: Rect


# Text slots the builder fills from what the agent passed; `footer`/`source` are
# filled by the builder itself and deliberately not advertised to the model.
TEXT_SLOTS = ("headline", "commentary", "kpi", "kpi_label", "subtitle", "footer", "source")
AGENT_SLOTS = ("headline", "chart", "table", "kpi", "kpi_label", "commentary")
SLOT_NAMES = (*TEXT_SLOTS, "chart", "table")
# The library slide's identity marker (see scripts/pack_scaffold.py). It is a
# slot only in the sense that the sync records where it is; the builder deletes
# the copy of it on every generated slide, so it never reaches a reader.
MARKER_SLOT = "slide_id"


@dataclass(frozen=True)
class Layout:
    """One entry in the catalogue the agent chooses from.

    Two generations live here on purpose. The ``predefined``/``layout_id`` +
    Rect fields are the s46 catalogue: predefined Slides layouts or a pack's
    master layouts, with explicit geometry. ``id``/``slots``/``chart_template``
    are the s48 template pack: a library slide the builder duplicates. The Rect
    fields are *derived* from ``slots`` for a pack layout, so every consumer that
    asks "does this layout take commentary?" keeps working unchanged.
    """

    name: str
    use_when: str
    predefined: str | None = None  # e.g. "TITLE_ONLY" when there is no pack
    # A master layout's objectId (source: layout / s46 packs). NOT the pack's
    # `L2`-style id, which is `id` below — the two address different things and
    # a slide-source pack has only the latter.
    layout_id: str | None = None
    chart: Rect | None = None
    table: Rect | None = None
    commentary: Rect | None = None
    kpi: Rect | None = None
    enabled: bool = True
    # -- s48 template pack ---------------------------------------------------
    id: str = ""  # "L2" — stable across renames; what the manifest records
    source: str = ""  # "slide" | "layout" | "" (built-in)
    slide_object_id: str = ""  # the library slide this layout duplicates
    slots: dict[str, Slot] = field(default_factory=dict)
    table_template: str = ""
    chart_template: dict[str, Any] | None = None
    series_max: int = 3
    grader_shape: str = ""

    @property
    def wants_chart(self) -> bool:
        return self.chart is not None

    @property
    def from_pack(self) -> bool:
        """True when this layout is built by duplicating a library slide."""
        return self.source == "slide" and bool(self.slide_object_id)

    def slot(self, name: str) -> Slot | None:
        return self.slots.get(name)

    def slot_names(self) -> list[str]:
        """The slots the agent can fill, in reading order."""
        if self.slots:
            return [n for n in AGENT_SLOTS if n in self.slots]
        legacy = [
            ("chart", self.chart),
            ("table", self.table),
            ("kpi", self.kpi),
            ("commentary", self.commentary),
        ]
        return ["headline", *[n for n, r in legacy if r is not None]]

    def layout_reference(self) -> dict[str, Any]:
        if self.layout_id:
            return {"layoutId": self.layout_id}
        return {"predefinedLayout": self.predefined or "TITLE_ONLY"}


# Content band beneath a themed title placeholder on a 10 x 5.625in page.
_BAND_TOP = 1.35
_BAND_H = 3.75

DEFAULT_CATALOGUE: tuple[Layout, ...] = (
    Layout(
        name="Title + Chart",
        use_when=(
            "One chart carries the whole answer. The default choice for a single "
            "trend, ranking or comparison."
        ),
        predefined="TITLE_ONLY",
        chart=Rect(0.6, _BAND_TOP, 8.8, _BAND_H),
    ),
    Layout(
        name="Chart + Commentary",
        use_when=(
            "A chart needs a sentence or two of interpretation beside it — what the "
            "shape means, not what it shows."
        ),
        predefined="TITLE_ONLY",
        commentary=Rect(0.6, _BAND_TOP, 3.0, _BAND_H),
        chart=Rect(3.9, _BAND_TOP, 5.5, _BAND_H),
    ),
    Layout(
        name="KPI + Chart",
        use_when=(
            "A single headline number is the answer and a chart explains how it got "
            "there. Put the number in `kpi` and keep it short (e.g. '$1.25m')."
        ),
        predefined="TITLE_ONLY",
        kpi=Rect(0.6, 1.5, 2.7, 1.3),
        commentary=Rect(0.6, 2.9, 2.7, 2.2),
        chart=Rect(3.6, _BAND_TOP, 5.8, _BAND_H),
    ),
    Layout(
        name="Table",
        use_when=(
            "The rows themselves are the answer — a ranking or breakdown a reader "
            "will want to scan or copy. Keep to about 10 rows."
        ),
        predefined="TITLE_ONLY",
        table=Rect(0.6, _BAND_TOP, 8.8, _BAND_H),
    ),
    Layout(
        name="Section",
        use_when="A divider between parts of a longer answer. Rare; only for 4+ slide decks.",
        predefined="SECTION_HEADER",
        commentary=None,
    ),
)


def render_layouts_md(catalogue: tuple[Layout, ...]) -> str:
    """The workspace file the agent Greps before naming a layout.

    Only enabled layouts appear — curation is what decides the agent's menu, so a
    withheld layout is invisible rather than merely discouraged.
    """
    lines = [
        "# Slide layouts",
        "",
        "The layouts `add_slide` accepts, by exact name. Pick the one that fits the",
        "content you are placing; when two fit, prefer the simpler one.",
        "",
    ]
    for layout in catalogue:
        if not layout.enabled:
            continue
        lines.append(f"## {layout.name}")
        lines.append("")
        lines.append(layout.use_when)
        lines.append("")
        lines.append("Accepts: " + ", ".join(layout.slot_names()))
        lines.append("")
    return "\n".join(lines)


async def load_catalogue(client: GoogleClient, template_id: str) -> tuple[Layout, ...]:
    """Read a curated pack's custom layouts, falling back to the built-in set.

    Geometry for chart/table slots comes from the pack's own placeholder shapes —
    cached here so a whole deck stays one batch per slide instead of paying a
    read-then-write round trip to discover where a chart belongs. There is no
    request that fills a CHART placeholder, so matching its geometry is the only
    way to land a chart in the slot a designer drew.
    """
    if not template_id:
        return DEFAULT_CATALOGUE
    doc = await client.get_presentation(template_id)
    layouts: list[Layout] = []
    for page in doc.get("layouts", []):
        props = page.get("layoutProperties") or {}
        name = str(props.get("displayName") or "").strip()
        if not name:
            continue
        chart_rect: Rect | None = None
        table_rect: Rect | None = None
        commentary_rect: Rect | None = None
        for element in page.get("pageElements", []):
            shape = element.get("shape") or {}
            ph_type = str((shape.get("placeholder") or {}).get("type") or "")
            rect = _rect_from_element(element)
            if rect is None:
                continue
            if ph_type == "CHART":
                chart_rect = rect
            elif ph_type == "TABLE":
                table_rect = rect
            elif ph_type in {"BODY", "SUBTITLE"}:
                commentary_rect = rect
        layouts.append(
            Layout(
                name=name,
                use_when=f"Layout '{name}' from the curated pack.",
                layout_id=str(page.get("objectId") or ""),
                chart=chart_rect,
                table=table_rect,
                commentary=commentary_rect,
            )
        )
    return tuple(layouts) or DEFAULT_CATALOGUE


def _rect_from_element(element: dict[str, Any]) -> Rect | None:
    size = element.get("size") or {}
    transform = element.get("transform") or {}
    try:
        w = float(size["width"]["magnitude"]) * float(transform.get("scaleX", 1))
        h = float(size["height"]["magnitude"]) * float(transform.get("scaleY", 1))
        x = float(transform.get("translateX", 0))
        y = float(transform.get("translateY", 0))
    except (KeyError, TypeError, ValueError):
        return None
    return Rect(x / EMU_PER_INCH, y / EMU_PER_INCH, w / EMU_PER_INCH, h / EMU_PER_INCH)


# ---------------------------------------------------------------------------
# Chart intent -> Sheets basicChart
# ---------------------------------------------------------------------------

CHART_TYPES = {
    "line": "LINE",
    "bar": "BAR",
    "column": "COLUMN",
    "area": "AREA",
    "scatter": "SCATTER",
}
DEFAULT_CHART_TYPE = "COLUMN"

# -- shape guard (s46 defect 1) ----------------------------------------------
#
# columns[0] is the x axis / label column. A line/area/column chart plotted
# against an x with duplicate values is not a trend — it is a spike train,
# every duplicate a vertical jag — and a bar/ranked chart with duplicate
# labels renders two bars fighting for the same slot. Both are silently
# "valid" to the Sheets API and meaningless on the slide, so the guard runs
# BEFORE any request is sent: a bad shape costs the model one turn instead of
# a broken deck.
LINE_LIKE_CHART_TYPES = {"line", "area", "column"}
BAR_CHART_TYPES = {"bar"}


def _chart_kind(layout: Layout, chart_type: str | None) -> str | None:
    """ "line" | "bar" | None (no chart / not covered by the guard).

    Pack-mode layouts have no ``chart_type`` argument — the shape is baked
    into the library chart the layout clones — so it is read off
    ``grader_shape`` instead: "ranked_set" is the one bar-family layout
    (Ranked Bars), everything else that carries a chart is a trend line.
    """
    if layout.from_pack:
        if "chart" not in layout.slots:
            return None
        return "bar" if layout.grader_shape == "ranked_set" else "line"
    if layout.chart is None:
        return None
    kind = (chart_type or "").strip().lower()
    if kind in BAR_CHART_TYPES:
        return "bar"
    if kind in LINE_LIKE_CHART_TYPES or not kind:
        return "line"
    return None  # e.g. scatter — duplicate x is a normal shape there


def _looks_numeric(value: Any) -> bool:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return True
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _dup_counts(rows: list[list[Any]], col_index: int) -> dict[Any, int]:
    counts: dict[Any, int] = {}
    for row in rows:
        key = row[col_index]
        counts[key] = counts.get(key, 0) + 1
    return counts


def _shape_error(tab_name: str, x_col: str, counts: dict[Any, int], *, kind: str) -> str:
    worst_x, worst_n = max(counts.items(), key=lambda kv: kv[1])
    name = tab_name or "frame"
    if kind == "bar":
        return (
            f"frame {name!r} has up to {worst_n} rows per label {x_col!r} "
            f"(e.g. {worst_x} ×{worst_n}): give each bar a unique label "
            "(aggregate the duplicates) before add_slide"
        )
    return (
        f"frame {name!r} has up to {worst_n} rows per {x_col!r} "
        f"(e.g. {worst_x} ×{worst_n}): aggregate to one row per x (or pass "
        "a categorical column to pivot into series) before add_slide"
    )


def _maybe_pivot_series(
    columns: list[str], rows: list[list[Any]], *, series_max: int
) -> tuple[list[str], list[list[Any]], str] | None:
    """Pivot ``x, category, value`` rows into one column per category.

    Only attempted for the common 3-column shape (x, a categorical second
    column, one measure): anything wider is ambiguous about which column to
    spread, so it is left to the reject message rather than guessed at. Returns
    ``None`` when the shape does not pivot cleanly — a category value repeated
    for the same x, or more distinct categories than the layout's series cap.
    """
    if len(columns) != 3:
        return None
    cat_values = [row[1] for row in rows]
    if all(_looks_numeric(v) for v in cat_values if v is not None):
        return None  # the second column looks numeric, not categorical
    categories = sorted({str(v) for v in cat_values})
    if not categories or len(categories) > series_max:
        return None
    grid: dict[tuple[Any, str], Any] = {}
    x_values: list[Any] = []
    seen_x: set[Any] = set()
    for x, cat, value in rows:
        key = (x, str(cat))
        if key in grid:
            return None  # duplicate (x, category) pair — not a clean grid
        grid[key] = value
        if x not in seen_x:
            seen_x.add(x)
            x_values.append(x)
    new_columns = [columns[0], *categories]
    new_rows = [[x, *[grid.get((x, cat), "") for cat in categories]] for x in x_values]
    return new_columns, new_rows, columns[1]


def _enforce_chart_shape(
    *,
    layout: Layout,
    chart_type: str | None,
    columns: list[str] | None,
    rows: list[list[Any]] | None,
    tab_name: str,
) -> tuple[list[str] | None, list[list[Any]] | None, str]:
    """Reject (or auto-pivot) a frame whose x/label column has duplicates.

    Returns the (possibly pivoted) ``columns``/``rows`` and ``pivoted_on`` (the
    category column name, or "" when nothing was pivoted). Raises ``ValueError``
    — caught by the tool handler and returned to the model as a structured
    error — when the shape cannot be built as-is or safely pivoted.
    """
    if not columns or not rows:
        return columns, rows, ""
    kind = _chart_kind(layout, chart_type)
    if kind is None:
        return columns, rows, ""
    counts = _dup_counts(rows, 0)
    if not counts or max(counts.values()) <= 1:
        return columns, rows, ""
    if kind == "line":
        pivot = _maybe_pivot_series(columns, rows, series_max=layout.series_max or 3)
        if pivot is not None:
            new_columns, new_rows, pivoted_on = pivot
            return new_columns, new_rows, pivoted_on
    raise ValueError(_shape_error(tab_name, columns[0], counts, kind=kind))


def _number_format(column: str) -> dict[str, Any] | None:
    """Sheets number format for a column, from the app's own unit vocabulary.

    Percent deliberately uses a NUMBER pattern with a literal '%' rather than
    Sheets' PERCENT type: the app's percent values are already in percentage
    points (4.5 means 4.5%), and PERCENT would multiply them by 100.
    """
    unit = unit_for_column(column)
    if unit == CURRENCY:
        return {"type": "CURRENCY", "pattern": '"$"#,##0'}
    if unit == PERCENT:
        return {"type": "NUMBER", "pattern": '#,##0.0"%"'}
    return {"type": "NUMBER", "pattern": "#,##0.##"}


# ---------------------------------------------------------------------------
# Text fit — the cover-overflow fix (s48 restyle item 1)
# ---------------------------------------------------------------------------

# Font ladders, largest first, per text slot. Sized from the scaffold's own
# defaults (scripts/pack_scaffold.py SEED_LAYOUTS): headline starts at the
# Cover's 28-34pt range, commentary/kpi_label are always small, kpi mirrors a
# headline-sized number. A ladder, not a formula, so a slide that already fits
# keeps the curator's intended size exactly rather than being nudged by a
# continuous function.
FONT_STEPS: dict[str, tuple[int, ...]] = {
    "headline": (28, 24, 20, 18, 16, 14),
    "commentary": (12, 11, 10),
    "kpi": (32, 28, 24, 20, 16, 14),
    "subtitle": (14, 13, 12, 11, 10),
}
FONT_FLOOR: dict[str, int] = {"headline": 14, "commentary": 10, "kpi": 14, "subtitle": 10}
# Inter/Arial semibold average glyph width, as a fraction of point size, and a
# single-spaced line height as a multiple of point size. Both are estimates —
# the Slides API exposes no text-measurement primitive — calibrated to catch
# the ~110-char cover headline that wrapped to 4 lines and clipped, not to
# reproduce the renderer's exact line breaks.
_AVG_GLYPH_WIDTH = 0.55
_LINE_HEIGHT = 1.2


def fit_font_size(text: str, rect: Rect, sizes: tuple[int, ...], *, floor: int) -> int:
    """The largest size in ``sizes`` (descending) whose estimated wrap fits ``rect``.

    chars-per-line = rect width in points / (avg glyph width * size); lines =
    ceil(len(text) / chars-per-line); a size is accepted when
    lines * line-height * size(in inches) <= rect height. Never returns smaller
    than ``floor`` — an unreadably tiny slide is a worse failure than a rare
    clipped last line on an extreme outlier.
    """
    text = text.strip()
    if not sizes:
        return floor
    if not text:
        return sizes[0]
    width_pt = rect.w * 72.0
    for size in sizes:
        chars_per_line = max(1, int(width_pt / (_AVG_GLYPH_WIDTH * size)))
        lines = -(-len(text) // chars_per_line)  # ceil
        if lines * _LINE_HEIGHT * size / 72.0 <= rect.h:
            return size
    return floor


def _slot_font_request(object_id: str, rect: Rect, name: str, text: str) -> dict[str, Any] | None:
    """The ``updateTextStyle`` step-down for one filled text slot, or ``None``
    when the slot isn't one of the ones this applies to (kpi_label, footer,
    source, subtitle's siblings keep their library-slide styling as authored).

    Deliberately NOT paired with a SHRINK_ON_OVERFLOW ``updateShapeProperties``
    autofit request: whether the Slides API even accepts ``autofit`` on a plain
    (non-placeholder) shape is unverified, and a rejected field would fail the
    whole atomic batch for the sake of a nicety the explicit step-down already
    covers.
    """
    sizes = FONT_STEPS.get(name)
    if not sizes or not text:
        return None
    size = fit_font_size(text, rect, sizes, floor=FONT_FLOOR[name])
    return {
        "updateTextStyle": {
            "objectId": object_id,
            "textRange": {"type": "ALL"},
            "style": {"fontSize": {"magnitude": size, "unit": "PT"}},
            "fields": "fontSize",
        }
    }


# ---------------------------------------------------------------------------
# Sheets tables + cloned chart specs (s48 §5)
# ---------------------------------------------------------------------------

# A Sheets Table's typed column, from the app's own unit vocabulary. A typed
# column is what makes the run Sheet an object a user can extend — sort, filter
# and a group-by all follow from the type — rather than a grid of strings.
_COLUMN_TYPES = {CURRENCY: "CURRENCY", PERCENT: "PERCENT", NUMBER: "DOUBLE"}
# Beyond these a frame gets its own tab and the Data block holds a pointer (§5).
MAX_BLOCK_COLUMNS = 26
MAX_BLOCK_ROWS = 2000
# Sheets rejects a table name with a space or punctuation; it is an identifier.
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9_]")


def sanitize_name(name: str) -> str:
    safe = _UNSAFE_NAME.sub("_", name).strip("_") or "frame"
    if safe[0].isdigit():
        safe = f"t_{safe}"
    return safe[:60]


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def column_type(column: str, values: list[Any]) -> str:
    """The Sheets Table column type for a column of already-written values.

    A numeric type is only claimed when *every* value is a number: a column of
    "1,234 (est.)" strings typed as DOUBLE is rejected by the API, and a column
    of numbers typed TEXT loses the formatting the whole exercise is for.
    """
    if values and all(_is_number(v) for v in values):
        return _COLUMN_TYPES.get(unit_for_column(column), "DOUBLE")
    return "TEXT"


def a1_column(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def a1_range(tab: str, row: int, rows: int, cols: int) -> str:
    """``'Data'!A5:D17`` for a block whose header is at 1-based ``row``."""
    return f"'{tab}'!A{row}:{a1_column(cols - 1)}{row + rows}"


def add_table_request(
    *,
    name: str,
    sheet_id: int,
    start_row: int,
    columns: list[str],
    rows: list[list[Any]],
) -> dict[str, Any]:
    """A Sheets v4 ``AddTableRequest`` over an already-written block.

    ``start_row`` is 0-based and points at the HEADER row: a Table's range
    includes its header, and Sheets derives the column names from it.

    Deliberately carries **no** ``columnProperties``. Sending them on the create
    corrupts the header row — verified against the live API: the names land one
    column to the right of where they belong, so ``month | avg_weekly_rent``
    comes back as ``month | month | avg_weekly_rent``. Types are applied by
    ``update_table_columns_request`` immediately afterwards, which is clean.
    """
    return {
        "addTable": {
            "table": {
                "name": name,
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": start_row,
                    "endRowIndex": start_row + len(rows) + 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": len(columns),
                },
            }
        }
    }


def update_table_columns_request(
    *, table_id: str, columns: list[str], rows: list[list[Any]]
) -> dict[str, Any]:
    """Type the Table's columns after it exists (see ``add_table_request``)."""
    return {
        "updateTable": {
            "table": {
                "tableId": table_id,
                "columnProperties": [
                    {
                        "columnIndex": i,
                        "columnName": str(column),
                        "columnType": column_type(str(column), [r[i] for r in rows if i < len(r)]),
                    }
                    for i, column in enumerate(columns)
                ],
            },
            "fields": "columnProperties",
        }
    }


def named_range_request(
    *, name: str, sheet_id: int, start_row: int, columns: int, rows: int
) -> dict[str, Any]:
    """The §5 fallback for an API that will not take ``addTable``.

    A named range is not a Table — no typed columns, no auto-expanding filter —
    but it keeps the one property the rest of the system depends on: the block
    is addressable by the same name the manifest records.
    """
    return {
        "addNamedRange": {
            "namedRange": {
                "name": name,
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": start_row,
                    "endRowIndex": start_row + rows + 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": columns,
                },
            }
        }
    }


# Fields a ChartSpec carries when READ that must not be sent back: derived
# state, data-source-only properties, and the deprecated Color twins of the
# ColorStyle fields (sending both is how a cloned spec starts failing).
_SPEC_DROP = ("dataSourceChartProperties", "filterSpecs", "sortSpecs", "backgroundColor")
_SERIES_DROP = ("color", "colorStyle")


def _grid(sheet_id: int, start_row: int, rows: int, col: int, end_col: int) -> dict[str, Any]:
    return {
        "sources": [
            {
                "sheetId": sheet_id,
                "startRowIndex": start_row,
                "endRowIndex": start_row + rows + 1,  # +1 for the header row
                "startColumnIndex": col,
                "endColumnIndex": end_col,
            }
        ]
    }


# Axis-title unit suffix (s48 restyle item 3): only percent and currency are
# unambiguous enough to say in three characters — a bare "number" column would
# just repeat the column name, which is noise the headline already carries.
_AXIS_UNIT_SUFFIX = {PERCENT: "%", CURRENCY: "$"}


def _axis_title(column: str) -> str | None:
    suffix = _AXIS_UNIT_SUFFIX.get(unit_for_column(column))
    return f"{column} ({suffix})" if suffix else None


def clone_chart_spec(
    template: dict[str, Any],
    *,
    sheet_id: int,
    start_row: int,
    rows: int,
    columns: int,
    series_max: int = 3,
    column_names: list[str] | None = None,
) -> dict[str, Any]:
    """The pack's hand-styled chart, pointed at this run's block.

    Cloning rather than composing is the whole design: chart styling is a
    designer's job and the API's chart vocabulary is enormous, so the curator
    styles one chart in Sheets and every generated chart inherits it. All this
    does is rewrite the GridRanges, cap the series count, drop the fields the
    API returns on a read but rejects on a write, and — when ``column_names``
    is given — label the value axis with its unit (item 3): a percent column
    plotted with a bare 0/5/10/15 axis reads as a raw count, not a rate.
    """
    spec = copy.deepcopy(template)
    for key in _SPEC_DROP:
        spec.pop(key, None)
    # The slide's headline is the chart's title (§4); the template's title only
    # exists so `_pack` can address it.
    spec["title"] = ""
    spec.pop("subtitle", None)
    basic = spec.get("basicChart")
    if not isinstance(basic, dict):
        return spec
    basic["domains"] = [{"domain": {"sourceRange": _grid(sheet_id, start_row, rows, 0, 1)}}]
    wanted = max(0, min(columns - 1, series_max or columns - 1))
    template_series = [s for s in basic.get("series") or [] if isinstance(s, dict)]
    series: list[dict[str, Any]] = []
    for i in range(wanted):
        # Reuse the template's i-th series styling, falling back to its last one
        # so a 3-series frame on a 1-series template still renders.
        base = copy.deepcopy(template_series[i] if i < len(template_series) else {})
        if not base and template_series:
            base = copy.deepcopy(template_series[-1])
            for key in _SERIES_DROP:
                base.pop(key, None)
        base.pop("color", None)
        base["series"] = {"sourceRange": _grid(sheet_id, start_row, rows, i + 1, i + 2)}
        series.append(base)
    basic["series"] = series
    if len(series) <= 1:
        basic["legendPosition"] = "NO_LEGEND"
    if column_names and len(column_names) > 1:
        title = _axis_title(column_names[1])
        axes = basic.get("axis")
        if title and isinstance(axes, list):
            # BAR is horizontal, so ITS value axis is BOTTOM, not LEFT — same
            # rule the pack's own template spec uses (pack_scaffold.py).
            value_axis = "BOTTOM_AXIS" if basic.get("chartType") == "BAR" else "LEFT_AXIS"
            for axis in axes:
                if isinstance(axis, dict) and axis.get("position") == value_axis:
                    axis["title"] = title
                    # Not `titleTextPosition`: Sheets 500s on an axis title sent
                    # with one (verified — see pack_scaffold.py's own note).
                    axis.pop("titleTextPosition", None)
    return spec


# ---------------------------------------------------------------------------
# The builder
# ---------------------------------------------------------------------------


@dataclass
class SlideRecord:
    """What one slide contains — the manifest the eval graders diff against.

    Two audiences, deliberately kept apart. The flat fields describe *content
    shape* and are what G5 asserts on; ``spec`` is the full record of what the
    agent chose — the template it picked and every option it passed — which is
    for a human reading back why a slide looks the way it does.

    Grading the two differently is the whole point: layout choice is a model
    decision free to vary between equally-correct runs, so asserting on it would
    grade a coin flip. Recording it costs nothing and answers the only question
    worth asking when a deck reads oddly — *what did it decide, and with what?*
    """

    index: int
    layout: str
    headline: str
    has_chart: bool
    has_table: bool
    has_kpi: bool
    sheet_tab: str | None
    rows: int
    # Content passed to add_slide that this layout has no region for. A layout is
    # a fixed set of rectangles, so asking "Title + Chart" to hold commentary has
    # nowhere to put it — but discarding the model's words in silence is the
    # wrong failure: it would look like a successful slide and read like a bug in
    # the deck. Recorded here so the tool result can say so and the model can
    # pick a layout that fits.
    dropped: tuple[str, ...] = ()
    # Everything the agent passed, verbatim, so the render is reproducible from
    # the manifest alone.
    commentary: str = ""
    kpi: str = ""
    chart_type: str = ""
    columns: tuple[str, ...] = ()
    frame: str = ""
    # -- s48 §6: what the change-log differ keys on. Stable field names.
    layout_id: str = ""  # "L2"
    slide_object_id: str = ""
    table_name: str = ""
    table_range: str = ""  # A1 including the tab, e.g. "'Data'!A5:D17"
    table_kind: str = ""  # table | range | tab
    chart_id: int | None = None
    chart_object_id: str | None = None
    query_ref: str = ""
    mart: str = ""
    notes_object_id: str = ""
    slide_url: str = ""
    # Set when add_slide's shape guard auto-pivoted a duplicate-x frame into
    # wide series (s46 defect 1) — the category column it spread, or "".
    pivoted_on: str = ""

    def manifest_row(self) -> list[Any]:
        """One Manifest tab row (§5). Column order is the contract."""
        return [
            self.index + 1,
            self.layout_id,
            self.layout,
            self.table_name,
            self.table_range,
            self.chart_id if self.chart_id is not None else "",
            self.headline,
            self.frame,
            self.query_ref,
            self.mart,
            self.rows,
            self.slide_url,
        ]

    def spec(self) -> dict[str, Any]:
        """The template picked and every option passed, as plain JSON."""
        return {
            "layout": self.layout,
            "headline": self.headline,
            "commentary": self.commentary,
            "kpi": self.kpi,
            "frame": self.frame,
            "columns": list(self.columns),
            "chart_type": self.chart_type,
            "rendered_as": ("chart" if self.has_chart else ("table" if self.has_table else "text")),
            "sheet_tab": self.sheet_tab,
            "rows": self.rows,
            "dropped": list(self.dropped),
            "pivoted_on": self.pivoted_on,
        }

    def flat(self) -> dict[str, Any]:
        """The manifest's flat, grader-facing view of one slide."""
        return {
            "index": self.index,
            "layout": self.layout,
            "layout_id": self.layout_id,
            "headline": self.headline,
            "has_chart": self.has_chart,
            "has_table": self.has_table,
            "has_kpi": self.has_kpi,
            "sheet_tab": self.sheet_tab,
            "rows": self.rows,
            "slide_object_id": self.slide_object_id,
            "table_name": self.table_name,
            "table_range": self.table_range,
            "table_kind": self.table_kind,
            "chart_id": self.chart_id,
            "chart_object_id": self.chart_object_id,
            "query_ref": self.query_ref,
            "mart": self.mart,
            "notes_object_id": self.notes_object_id,
            "slide_url": self.slide_url,
            "spec": self.spec(),
        }


@dataclass
class DeckBuilder:
    """Creates one Sheet + one deck per run and appends slides to them.

    Two builds live here, chosen per layout by ``Layout.from_pack``:

    * **pack mode** (s48) — the deck starts as a *copy of Pack.slides*, so every
      library slide travels with it; each ``add_slide`` duplicates one library
      slide, fills its slots and moves it to the end, and ``finish`` deletes the
      library slides that are left. One batch per slide, still atomic.
    * **legacy mode** (s46) — ``createSlide`` from a predefined or master layout
      plus explicit geometry. Still the path when there is no pack, which is what
      keeps the feature working before anyone has built one.

    The run Sheet is the same in both modes except that pack mode writes one
    ``Data`` tab of Sheets **Tables** instead of a tab per frame (§5).
    """

    client: GoogleClient
    catalogue: tuple[Layout, ...]
    title: str
    presentation_id: str = ""
    spreadsheet_id: str = ""
    template_id: str = ""
    slides: list[SlideRecord] = field(default_factory=list)
    # -- s48 -----------------------------------------------------------------
    run_id: str = ""
    question: str = ""
    pack_name: str = ""
    pack_version: int = 0
    pack_sheet_id: str = ""
    table_templates: dict[str, Any] = field(default_factory=dict)
    folder_id: str = ""
    _tab_names: set[str] = field(default_factory=set)
    _data_sheet_id: int = 0
    _data_row: int = 0  # 0-based cursor into the Data tab
    _library_slides: list[str] = field(default_factory=list)
    _library_notes: dict[str, str] = field(default_factory=dict)
    _chart_specs: dict[int, dict[str, Any]] = field(default_factory=dict)
    _table_kind: str = "table"  # falls back to "range" the first time addTable is refused

    # -- catalogue -----------------------------------------------------------

    def layout(self, name: str) -> Layout | None:
        wanted = name.strip().casefold()
        for entry in self.catalogue:
            if entry.enabled and entry.name.casefold() == wanted:
                return entry
        return None

    def layout_by_id(self, layout_id: str) -> Layout | None:
        """By pack id, *ignoring* ``enabled`` — how the builder reaches a layout
        the agent must not choose itself (L9 Sources & SQL is auto-appended)."""
        for entry in self.catalogue:
            if entry.id == layout_id:
                return entry
        return None

    def layout_names(self) -> list[str]:
        return [entry.name for entry in self.catalogue if entry.enabled]

    @property
    def pack_mode(self) -> bool:
        return any(entry.from_pack for entry in self.catalogue)

    # -- lifecycle -----------------------------------------------------------

    async def start(self, template_id: str) -> None:
        """Create the run's Sheet and deck.

        In pack mode the deck is a copy of Pack.slides and its library slides are
        deliberately KEPT: they are the stock the builder duplicates from, and
        ``finish`` removes what is left. In legacy mode the copy's slides are
        cleared immediately, exactly as in s46.
        """
        self.template_id = template_id
        self.folder_id = await _runs_folder(self.client)
        await self._start_sheet()
        if template_id:
            self.presentation_id = await self.client.copy_file(
                template_id, self.title, app_properties=self._app_properties("deck")
            )
        else:
            self.presentation_id = await self.client.create_presentation(self.title)
        await self._file_into_folder(self.presentation_id, "deck")
        if self.pack_mode:
            doc = await self.client.get_presentation_pages(
                self.presentation_id,
                fields=(
                    "slides(objectId,"
                    "slideProperties.notesPage.notesProperties.speakerNotesObjectId)"
                ),
            )
            for slide in doc.get("slides", []):
                oid = str(slide.get("objectId"))
                self._library_slides.append(oid)
                notes = (
                    ((slide.get("slideProperties") or {}).get("notesPage") or {}).get(
                        "notesProperties"
                    )
                    or {}
                ).get("speakerNotesObjectId")
                if notes:
                    self._library_notes[oid] = str(notes)
            return
        doc = await self.client.get_presentation(self.presentation_id)
        existing = [str(s.get("objectId")) for s in doc.get("slides", [])]
        if existing:
            await self.client.slides_batch(
                self.presentation_id,
                [{"deleteObject": {"objectId": oid}} for oid in existing],
            )

    async def _file_into_folder(self, file_id: str, kind: str) -> None:
        """Tag a generated file and file it under ``Data Pilot/runs``.

        Best-effort on purpose: a deck that is correct but sitting in My Drive
        root is a tidiness problem, and failing the answer over it would be
        absurd. The appProperties, which the poller needs, are attempted first.
        """
        with contextlib.suppress(Exception):
            await self.client.update_file(
                file_id,
                app_properties=self._app_properties(kind),
                add_parents=self.folder_id or "",
            )

    def _app_properties(self, kind: str) -> dict[str, str]:
        """Drive metadata every generated file carries (§5).

        This is what lets the handover poller find a run's artifacts, and what
        makes a deck self-describing months later: which run made it, from which
        pack version.
        """
        props = {"dp_kind": kind}
        if self.run_id:
            props["dp_run_id"] = self.run_id
        if self.pack_name:
            props["dp_pack"] = self.pack_name
            props["dp_pack_version"] = str(self.pack_version)
        return props

    async def _start_sheet(self) -> None:
        """One Sheet per run, created fresh: Data, Manifest, README (§5)."""
        self.spreadsheet_id, first = await self.client.create_spreadsheet(f"{self.title} — data")
        await self._file_into_folder(self.spreadsheet_id, "sheet")
        if not self.pack_mode:
            return
        out = await self.client.sheets_batch(
            self.spreadsheet_id,
            [
                {
                    "updateSheetProperties": {
                        "properties": {"sheetId": first, "title": "Data"},
                        "fields": "title",
                    }
                },
                {"addSheet": {"properties": {"title": "Manifest"}}},
                {"addSheet": {"properties": {"title": "README"}}},
            ],
        )
        del out
        self._data_sheet_id = first
        self._tab_names.update({"Data", "Manifest", "README"})
        await self.client.write_values(
            self.spreadsheet_id, "'Manifest'!A1", [list(MANIFEST_COLUMNS)]
        )

    # -- the Data tab --------------------------------------------------------

    async def _write_block(
        self,
        *,
        index: int,
        layout: Layout,
        headline: str,
        columns: list[str],
        rows: list[list[Any]],
        frame: str,
        slide_object_id: str,
    ) -> dict[str, Any]:
        """One slide's rows in the run Sheet, as a named Sheets Table (§5).

        Returns the block's coordinates so the chart can be pointed at it and the
        manifest can name it. A frame too wide or too long for the Data tab gets
        its own tab and the Data block keeps a one-line pointer, because a 3000
        row block would bury every other slide's data below the fold.
        """
        name = sanitize_name(f"s{index + 1:02d}_{frame or 'data'}")
        overflow = len(columns) > MAX_BLOCK_COLUMNS or len(rows) > MAX_BLOCK_ROWS
        title_text = f"#{index + 1} · {layout.name} — {headline}"
        title_cell = (
            f'=HYPERLINK("{self._slide_url(slide_object_id)}","{title_text.replace(chr(34), "")}")'
        )

        if overflow:
            tab = self._unique_tab(name)
            sheet_id = await self.client.add_sheet(self.spreadsheet_id, tab)
            header_row = 0
            await self.client.write_values(
                self.spreadsheet_id, f"'Data'!A{self._data_row + 1}", [[title_cell]], raw=False
            )
            await self.client.write_values(
                self.spreadsheet_id,
                f"'Data'!A{self._data_row + 2}",
                [[f"{len(rows)} rows — see the '{tab}' tab"]],
            )
            self._data_row += 4
        else:
            tab = "Data"
            sheet_id = self._data_sheet_id
            await self.client.write_values(
                self.spreadsheet_id, f"'Data'!A{self._data_row + 1}", [[title_cell]], raw=False
            )
            header_row = self._data_row + 1
            self._data_row = header_row + len(rows) + 3  # header + rows + two blank

        await self.client.write_values(
            self.spreadsheet_id,
            f"'{tab}'!A{header_row + 1}",
            [list(columns), *rows],
        )
        table_range = a1_range(tab, header_row + 1, len(rows), len(columns))
        kind = await self._name_block(
            name=name, sheet_id=sheet_id, start_row=header_row, columns=columns, rows=rows
        )
        await self._format_block(sheet_id, header_row, columns)
        return {
            "name": name,
            "range": table_range,
            "kind": "tab" if overflow else kind,
            "sheet_id": sheet_id,
            "start_row": header_row,
            "tab": tab,
        }

    async def _name_block(
        self,
        *,
        name: str,
        sheet_id: int,
        start_row: int,
        columns: list[str],
        rows: list[list[Any]],
    ) -> str:
        """``addTable``, falling back to a named range (§5).

        The fallback is not defensive padding: Sheets Tables are a recent
        feature, and an account or edition without them would otherwise fail the
        whole run over presentation sugar. Once refused it stays refused for the
        rest of the run rather than paying the error on every slide, and
        ``table_kind`` in the manifest records which a block actually got.
        """
        if self._table_kind == "table":
            try:
                out = await self.client.sheets_batch(
                    self.spreadsheet_id,
                    [
                        add_table_request(
                            name=name,
                            sheet_id=sheet_id,
                            start_row=start_row,
                            columns=columns,
                            rows=rows,
                        )
                    ],
                )
                table_id = str(
                    ((out.get("replies") or [{}])[0].get("addTable") or {})
                    .get("table", {})
                    .get("tableId", "")
                )
                if table_id:
                    # Typing is a second request on purpose — see add_table_request.
                    with contextlib.suppress(Exception):
                        await self.client.sheets_batch(
                            self.spreadsheet_id,
                            [
                                update_table_columns_request(
                                    table_id=table_id, columns=columns, rows=rows
                                )
                            ],
                        )
                return "table"
            except Exception as exc:  # noqa: BLE001 — the fallback IS the handling
                print(f"[data-agent] addTable refused ({exc}); using named ranges")
                self._table_kind = "range"
        with contextlib.suppress(Exception):
            await self.client.sheets_batch(
                self.spreadsheet_id,
                [
                    named_range_request(
                        name=name,
                        sheet_id=sheet_id,
                        start_row=start_row,
                        columns=len(columns),
                        rows=len(rows),
                    )
                ],
            )
        return "range"

    async def _format_block(self, sheet_id: int, header_row: int, columns: list[str]) -> None:
        requests: list[dict[str, Any]] = [
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": header_row,
                        "endRowIndex": header_row + 1,
                    },
                    "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                    "fields": "userEnteredFormat.textFormat.bold",
                }
            }
        ]
        for idx, column in enumerate(columns):
            if idx == 0:
                continue  # the domain column stays as written (dates, labels)
            fmt = _number_format(column)
            if fmt is None:
                continue
            requests.append(
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": header_row + 1,
                            "startColumnIndex": idx,
                            "endColumnIndex": idx + 1,
                        },
                        "cell": {"userEnteredFormat": {"numberFormat": fmt}},
                        "fields": "userEnteredFormat.numberFormat",
                    }
                }
            )
        with contextlib.suppress(Exception):
            await self.client.sheets_batch(self.spreadsheet_id, requests)

    async def _template_spec(self, chart_template: dict[str, Any]) -> dict[str, Any] | None:
        """The pack's chart spec, read once per process-lifetime of this builder."""
        chart_id = int(chart_template.get("chart_id") or 0)
        if not chart_id or not self.pack_sheet_id:
            return None
        if chart_id in self._chart_specs:
            return self._chart_specs[chart_id]
        doc = await self.client.get_spreadsheet(
            self.pack_sheet_id, fields="sheets.charts(chartId,spec)"
        )
        for sheet in doc.get("sheets", []):
            for chart in sheet.get("charts", []) or []:
                self._chart_specs[int(chart.get("chartId", 0))] = dict(chart.get("spec") or {})
        return self._chart_specs.get(chart_id)

    async def _clone_chart(
        self, *, layout: Layout, block: dict[str, Any], columns: list[str], rows: list[list[Any]]
    ) -> int | None:
        if not layout.chart_template:
            return None
        template = await self._template_spec(layout.chart_template)
        if template is None:
            return None
        spec = clone_chart_spec(
            template,
            sheet_id=int(block["sheet_id"]),
            start_row=int(block["start_row"]),
            rows=len(rows),
            columns=len(columns),
            series_max=layout.series_max,
            column_names=columns,
        )
        out = await self.client.sheets_batch(
            self.spreadsheet_id,
            [
                {
                    "addChart": {
                        "chart": {
                            "spec": spec,
                            "position": {
                                "overlayPosition": {
                                    "anchorCell": {
                                        "sheetId": int(block["sheet_id"]),
                                        "rowIndex": int(block["start_row"]),
                                        "columnIndex": len(columns) + 2,
                                    }
                                }
                            },
                        }
                    }
                }
            ],
        )
        return int(out["replies"][0]["addChart"]["chart"]["chartId"])

    # -- slides --------------------------------------------------------------

    def _slide_url(self, slide_object_id: str) -> str:
        return f"{deck_url(self.presentation_id)}#slide=id.{slide_object_id}"

    async def add_slide(
        self,
        *,
        layout: Layout,
        headline: str,
        commentary: str = "",
        kpi: str = "",
        kpi_label: str = "",
        columns: list[str] | None = None,
        rows: list[list[Any]] | None = None,
        chart_type: str | None = None,
        tab_name: str = "",
        query_ref: str = "",
        mart: str = "",
    ) -> SlideRecord:
        columns, rows, pivoted_on = _enforce_chart_shape(
            layout=layout,
            chart_type=chart_type,
            columns=columns,
            rows=rows,
            tab_name=tab_name,
        )
        if layout.from_pack:
            record = await self._add_pack_slide(
                layout=layout,
                headline=headline,
                commentary=commentary,
                kpi=kpi,
                kpi_label=kpi_label,
                columns=columns,
                rows=rows,
                tab_name=tab_name,
                query_ref=query_ref,
                mart=mart,
            )
        else:
            record = await self._add_legacy_slide(
                layout=layout,
                headline=headline,
                commentary=commentary,
                kpi=kpi,
                columns=columns,
                rows=rows,
                chart_type=chart_type,
                tab_name=tab_name,
            )
        if pivoted_on:
            record.pivoted_on = pivoted_on
        return record

    async def _add_pack_slide(
        self,
        *,
        layout: Layout,
        headline: str,
        commentary: str = "",
        kpi: str = "",
        kpi_label: str = "",
        columns: list[str] | None = None,
        rows: list[list[Any]] | None = None,
        tab_name: str = "",
        query_ref: str = "",
        mart: str = "",
        slide_rows: list[list[Any]] | None = None,
    ) -> SlideRecord:
        """One library slide, duplicated and filled in a single atomic batch.

        Order matters and is the reason this is one request list: the duplicate
        must exist before anything targets its slots, the chart must exist in the
        Sheet before ``createSheetsChart`` can embed it, and the placeholder
        shape must be deleted in the same batch that puts the real chart where it
        was — otherwise a failure leaves a slide with "[chart]" written on it.
        """
        index = len(self.slides)
        prefix = f"s{index + 1:02d}"
        slide_id = f"{prefix}_slide"

        block: dict[str, Any] = {}
        chart_id: int | None = None
        wants_data = bool(columns and rows) and ("chart" in layout.slots or "table" in layout.slots)
        if wants_data:
            assert columns is not None and rows is not None
            block = await self._write_block(
                index=index,
                layout=layout,
                headline=headline,
                columns=columns,
                rows=rows,
                frame=tab_name,
                slide_object_id=slide_id,
            )
            if "chart" in layout.slots:
                chart_id = await self._clone_chart(
                    layout=layout, block=block, columns=columns, rows=rows
                )

        ids = {layout.slide_object_id: slide_id}
        for name, slot in layout.slots.items():
            ids[slot.object_id] = f"{prefix}_{name}"
        notes_id = ""
        source_notes = self._library_notes.get(layout.slide_object_id)
        if source_notes:
            notes_id = f"{prefix}_notes"
            ids[source_notes] = notes_id

        requests: list[dict[str, Any]] = [
            {"duplicateObject": {"objectId": layout.slide_object_id, "objectIds": ids}},
            {
                "updateSlidesPosition": {
                    "slideObjectIds": [slide_id],
                    "insertionIndex": len(self._library_slides) + index,
                }
            },
        ]

        texts = {
            "headline": headline,
            "commentary": commentary,
            "kpi": kpi,
            "kpi_label": kpi_label,
            "subtitle": self.question,
            "footer": self._footer_text(),
            "source": self._source_text(mart),
        }
        for name in TEXT_SLOTS:
            if name not in layout.slots:
                continue
            oid = f"{prefix}_{name}"
            # deleteText first, unconditionally: the library slide carries sample
            # copy, and a slot the agent left empty must come out EMPTY rather
            # than showing the template's "The finding, stated as a sentence".
            requests.append({"deleteText": {"objectId": oid, "textRange": {"type": "ALL"}}})
            text = str(texts.get(name) or "")
            if text:
                requests.append({"insertText": {"objectId": oid, "text": text}})
                font_request = _slot_font_request(oid, layout.slots[name].rect, name, text)
                if font_request is not None:
                    requests.append(font_request)

        if MARKER_SLOT in layout.slots:
            requests.append({"deleteObject": {"objectId": f"{prefix}_{MARKER_SLOT}"}})

        chart_object_id: str | None = None
        if "chart" in layout.slots:
            requests.append({"deleteObject": {"objectId": f"{prefix}_chart"}})
            if chart_id is not None:
                chart_object_id = f"{prefix}_chartobj"
                requests.append(
                    {
                        "createSheetsChart": {
                            "objectId": chart_object_id,
                            "spreadsheetId": self.spreadsheet_id,
                            "chartId": chart_id,
                            # LINKED, not NOT_LINKED_IMAGE: the chart stays tied
                            # to the Sheet and can be refreshed rather than
                            # rebuilt. It still renders for a viewer with no
                            # access to the Sheet — a cached server-side render.
                            "linkingMode": "LINKED",
                            "elementProperties": layout.slots["chart"].rect.element_properties(
                                slide_id
                            ),
                        }
                    }
                )
        table_drawn = False
        if "table" in layout.slots:
            requests.append({"deleteObject": {"objectId": f"{prefix}_table"}})
            body = slide_rows if slide_rows is not None else rows
            if columns and body:
                table_drawn = True
                requests.extend(
                    _table_requests(
                        f"{prefix}_tableobj",
                        layout.slots["table"].rect,
                        slide_id,
                        columns,
                        body,
                        font_pt=TABLE_FONT_PT.get(layout.id, DEFAULT_TABLE_FONT_PT),
                    )
                )
        if notes_id and commentary:
            requests.append({"insertText": {"objectId": notes_id, "text": commentary}})

        dropped = tuple(
            what
            for what, given, placed in (
                ("commentary", bool(commentary), "commentary" in layout.slots),
                ("kpi", bool(kpi), "kpi" in layout.slots),
                ("data", bool(columns and rows), bool(block)),
            )
            if given and not placed
        )

        await self.client.slides_batch(self.presentation_id, requests)
        record = SlideRecord(
            index=index,
            layout=layout.name,
            headline=headline,
            has_chart=chart_id is not None,
            has_table=table_drawn,
            has_kpi=bool(kpi and "kpi" in layout.slots),
            sheet_tab=str(block.get("tab") or "") or None,
            rows=len(rows or []),
            dropped=dropped,
            commentary=commentary,
            kpi=kpi,
            chart_type="",
            columns=tuple(columns or ()),
            frame=tab_name,
            layout_id=layout.id,
            slide_object_id=slide_id,
            table_name=str(block.get("name") or ""),
            table_range=str(block.get("range") or ""),
            table_kind=str(block.get("kind") or ""),
            chart_id=chart_id,
            chart_object_id=chart_object_id,
            query_ref=query_ref,
            mart=mart,
            notes_object_id=notes_id,
            slide_url=self._slide_url(slide_id),
        )
        self.slides.append(record)
        return record

    def _footer_text(self) -> str:
        short = (self.run_id or "")[:8]
        today = datetime.now(UTC).date().isoformat()
        return f"Data Pilot · {short} · {today}" if short else f"Data Pilot · {today}"

    def _source_text(self, mart: str) -> str:
        marts = mart or ", ".join(sorted({s.mart for s in self.slides if s.mart})) or "the marts"
        return f"Source: {marts}; SQL in the Sheet's Manifest tab"

    async def _add_legacy_slide(
        self,
        *,
        layout: Layout,
        headline: str,
        commentary: str = "",
        kpi: str = "",
        columns: list[str] | None = None,
        rows: list[list[Any]] | None = None,
        chart_type: str | None = None,
        tab_name: str = "",
    ) -> SlideRecord:
        """The s46 build: create a slide from a layout reference and place
        explicit geometry on it. Unchanged behaviour — this is what runs when
        there is no pack."""
        index = len(self.slides)
        slide_id = f"slide_{index}"
        title_id = f"title_{index}"
        requests: list[dict[str, Any]] = [
            {
                "createSlide": {
                    "objectId": slide_id,
                    "insertionIndex": index,
                    "slideLayoutReference": layout.layout_reference(),
                    "placeholderIdMappings": [
                        {
                            "layoutPlaceholder": {"type": "TITLE", "index": 0},
                            "objectId": title_id,
                        }
                    ],
                }
            },
            {"insertText": {"objectId": title_id, "text": headline}},
        ]

        chart_id: int | None = None
        sheet_tab: str | None = None
        row_count = 0
        if columns and rows and layout.wants_chart:
            sheet_tab = self._unique_tab(tab_name or f"slide{index + 1}")
            row_count = len(rows)
            sheet_id = await self._write_frame(sheet_tab, columns, rows)
            chart_id = await self._add_chart(
                sheet_id=sheet_id,
                title=headline,
                chart_type=CHART_TYPES.get((chart_type or "").lower(), DEFAULT_CHART_TYPE),
                row_count=row_count,
                col_count=len(columns),
            )
            requests.append(
                {
                    "createSheetsChart": {
                        "objectId": f"chart_{index}",
                        "spreadsheetId": self.spreadsheet_id,
                        "chartId": chart_id,
                        "linkingMode": "LINKED",
                        "elementProperties": layout.chart.element_properties(slide_id),  # type: ignore[union-attr]
                    }
                }
            )
        elif columns and rows and layout.table is not None:
            sheet_tab = self._unique_tab(tab_name or f"slide{index + 1}")
            row_count = len(rows)
            await self._write_frame(sheet_tab, columns, rows)
            requests.extend(
                _table_requests(f"table_{index}", layout.table, slide_id, columns, rows)
            )

        if kpi and layout.kpi is not None:
            requests.extend(
                _text_box_requests(f"kpi_{index}", layout.kpi, slide_id, kpi, size=28, bold=True)
            )
        if commentary and layout.commentary is not None:
            requests.extend(
                _text_box_requests(
                    f"note_{index}", layout.commentary, slide_id, commentary, size=12
                )
            )

        dropped = tuple(
            what
            for what, given, placed in (
                ("commentary", bool(commentary), layout.commentary is not None),
                ("kpi", bool(kpi), layout.kpi is not None),
                ("data", bool(columns and rows), sheet_tab is not None),
            )
            if given and not placed
        )

        await self.client.slides_batch(self.presentation_id, requests)
        record = SlideRecord(
            index=index,
            layout=layout.name,
            headline=headline,
            has_chart=chart_id is not None,
            has_table=sheet_tab is not None and chart_id is None,
            has_kpi=bool(kpi and layout.kpi is not None),
            sheet_tab=sheet_tab,
            rows=row_count,
            dropped=dropped,
            commentary=commentary,
            kpi=kpi,
            chart_type=(chart_type or "") if chart_id is not None else "",
            columns=tuple(columns or ()),
            frame=tab_name,
            layout_id=layout.id,
            slide_object_id=slide_id,
            chart_id=chart_id,
            chart_object_id=f"chart_{index}" if chart_id is not None else None,
            slide_url=self._slide_url(slide_id) if self.presentation_id else "",
        )
        self.slides.append(record)
        return record

    async def _write_frame(self, tab: str, columns: list[str], rows: list[list[Any]]) -> int:
        """Write a frame to its own tab and format its numeric columns."""
        sheet_id = await self.client.add_sheet(self.spreadsheet_id, tab)
        await self.client.write_values(self.spreadsheet_id, f"'{tab}'!A1", [list(columns), *rows])
        await self._format_block(sheet_id, 0, columns)
        return sheet_id

    async def _add_chart(
        self,
        *,
        sheet_id: int,
        title: str,
        chart_type: str,
        row_count: int,
        col_count: int,
    ) -> int:
        """Add a native chart over the tab's range; return its chartId."""
        series = [
            {
                "series": {"sourceRange": _grid(sheet_id, 0, row_count, i, i + 1)},
                "targetAxis": "BOTTOM_AXIS" if chart_type == "BAR" else "LEFT_AXIS",
            }
            for i in range(1, col_count)
        ]
        spec = {
            "title": title,
            "basicChart": {
                "chartType": chart_type,
                "legendPosition": "BOTTOM_LEGEND" if len(series) > 1 else "NO_LEGEND",
                "headerCount": 1,
                "domains": [{"domain": {"sourceRange": _grid(sheet_id, 0, row_count, 0, 1)}}],
                "series": series,
            },
        }
        out = await self.client.sheets_batch(
            self.spreadsheet_id,
            [
                {
                    "addChart": {
                        "chart": {
                            "spec": spec,
                            "position": {
                                "overlayPosition": {
                                    "anchorCell": {
                                        "sheetId": sheet_id,
                                        "rowIndex": 1,
                                        "columnIndex": col_count + 1,
                                    }
                                }
                            },
                        }
                    }
                }
            ],
        )
        return int(out["replies"][0]["addChart"]["chart"]["chartId"])

    def _unique_tab(self, base: str) -> str:
        name = base[:80] or "data"
        n = 2
        while name in self._tab_names:
            name = f"{base[:76]}_{n}"
            n += 1
        self._tab_names.add(name)
        return name

    # -- finishing -----------------------------------------------------------

    async def finish(self, sources: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Close the run: append Sources & SQL, clear the library, write the Sheet.

        Returns the version-1 **baseline snapshot** (§7) — the normalised shape
        the handover poller later diffs a human's edits against. Taken here, by
        the builder, because only the builder knows the deck is finished; the
        poller only ever diffs.
        """
        if not self.pack_mode:
            return {}
        try:
            await self._append_sources(sources or [])
        except Exception as exc:  # noqa: BLE001 — a deck missing Sources is still a deck
            print(f"[data-agent] Sources & SQL slide not appended ({exc})")
        if self._library_slides:
            try:
                await self.client.slides_batch(
                    self.presentation_id,
                    [{"deleteObject": {"objectId": oid}} for oid in self._library_slides],
                )
            except Exception as exc:  # noqa: BLE001 — leftover library slides beat a crash
                print(f"[data-agent] library slides not cleared ({exc})")
            self._library_slides = []
        with contextlib.suppress(Exception):
            await self._write_sheet_tabs()
        try:
            return await snapshot_artifacts(
                self.client,
                presentation_id=self.presentation_id,
                spreadsheet_id=self.spreadsheet_id,
                manifest=self.manifest(),
            )
        except Exception as exc:  # noqa: BLE001 — a missing baseline is not a failed deck
            print(f"[data-agent] baseline snapshot skipped ({exc})")
            return {}

    # (item 4) full SQL always lives in the Sheet's Manifest tab (`rows`, sent
    # to `_write_block`); the on-slide table only ever shows a first-line taste.
    _SOURCES_SQL_CHARS = 80
    _SOURCES_NOTE = "Full SQL in the Sheet's Manifest tab"

    async def _append_sources(self, sources: list[dict[str, Any]]) -> None:
        """The auto-appended L9 slide: every query this deck rests on.

        Not optional and not the agent's choice: a deck of conclusions with no
        route back to the SQL is exactly the artifact this whole system exists to
        replace.
        """
        layout = self.layout_by_id("L9")
        if layout is None or not layout.from_pack or not sources:
            return
        columns = ["query_ref", "mart", "rows", "sql"]
        rows = [
            [s.get("ref", ""), s.get("mart", ""), s.get("rows", 0), str(s.get("sql") or "")]
            for s in sources
        ]
        slide_rows = [
            [r[0], r[1], r[2], _shorten(str(r[3]), self._SOURCES_SQL_CHARS)] for r in rows
        ]
        # L9 has no `commentary` slot (SEED_LAYOUTS) to carry the pointer to the
        # full SQL — `source` is auto-filled by the builder itself and not free
        # for this — so it goes in as the table's own last row instead, trimmed
        # to fit inside the same row cap the rest of the table obeys, so the
        # pointer itself is never the row that overflows.
        has_commentary_slot = "commentary" in layout.slots
        commentary = self._SOURCES_NOTE if has_commentary_slot else ""
        if not has_commentary_slot:
            table_slot = layout.slot("table")
            cap = (
                rows_that_fit(
                    table_slot.rect.h, font_pt=TABLE_FONT_PT.get(layout.id, DEFAULT_TABLE_FONT_PT)
                )
                if table_slot
                else len(slide_rows)
            )
            slide_rows = [*slide_rows[: max(0, cap - 1)], ["", "", "", self._SOURCES_NOTE]]
        await self._add_pack_slide(
            layout=layout,
            headline="Sources & SQL",
            commentary=commentary,
            columns=columns,
            rows=rows,
            tab_name="sources",
            slide_rows=slide_rows,
        )

    async def _write_sheet_tabs(self) -> None:
        rows = [s.manifest_row() for s in self.slides]
        if rows:
            await self.client.write_values(self.spreadsheet_id, "'Manifest'!A2", rows)
        readme = [
            ["Data Pilot — run workbook"],
            ["question", self.question],
            ["run id", self.run_id],
            ["pack", f"{self.pack_name} v{self.pack_version}" if self.pack_name else "built-in"],
            ["deck", deck_url(self.presentation_id)],
            [],
            ["How to extend"],
            ["1. File > Make a copy — this workbook is yours to change."],
            ["2. Edit a Table on the Data tab; its chart follows automatically."],
            ["3. Each Table's name and range are listed on the Manifest tab."],
        ]
        await self.client.write_values(self.spreadsheet_id, "'README'!A1", readme)

    async def publish(self) -> dict[str, Any]:
        """Share both artifacts read-only and return the URLs.

        Public sharing steps outside RLS permanently, so the caller gates this —
        it is never reached unless the runtime is configured to allow it.
        """
        await self.client.share_public(self.presentation_id)
        await self.client.share_public(self.spreadsheet_id)
        return self.manifest()

    def manifest(self) -> dict[str, Any]:
        return {
            "deck_url": deck_url(self.presentation_id),
            # The chrome-free viewer the answer area embeds (s46 D3); deck_url is
            # the link out for someone who wants to edit or copy it.
            "embed_url": deck_embed_url(self.presentation_id),
            "sheet_url": sheet_url(self.spreadsheet_id),
            "presentation_id": self.presentation_id,
            "spreadsheet_id": self.spreadsheet_id,
            "folder_id": self.folder_id,
            "pack": (
                {"name": self.pack_name, "version": self.pack_version} if self.pack_name else None
            ),
            "slides": [s.flat() for s in self.slides],
            # Which layouts this deck used, in order — the one-line answer to
            # "what did the agent choose?" without walking every slide.
            "layouts_used": [s.layout for s in self.slides],
            # The menu it chose from. A deck is only interpretable against the
            # catalogue that was live when it was built: a slide that looks like
            # a poor choice may have been the only layout enabled at the time.
            "catalogue": self.layout_names(),
            "template_pack": self.template_id or "built-in catalogue",
        }


# One shared "Data Pilot runs" folder per generating account, resolved once per
# process: every run's two artifacts land there instead of in the Drive root.
_RUNS_FOLDER: dict[str, str] = {}


async def _runs_folder(client: GoogleClient) -> str:
    """Find-or-create ``Data Pilot runs``. Returns "" if Drive says no."""
    if "id" in _RUNS_FOLDER:
        return _RUNS_FOLDER["id"]
    props = {"dp_kind": "runs_folder"}
    try:
        found = await client.find_files(app_properties=props, mime_type=FOLDER_MIME)
        folder = (
            str(found[0]["id"])
            if found
            else await client.create_folder("Data Pilot runs", app_properties=props)
        )
    except Exception as exc:  # noqa: BLE001 — filing is a nicety, not the deliverable
        # Deliberately NOT cached: a transient Drive error must not condemn every
        # later run in this process to the Drive root.
        print(f"[data-agent] runs folder unavailable ({exc}); artifacts stay in Drive root")
        return ""
    _RUNS_FOLDER["id"] = folder
    return folder


MANIFEST_COLUMNS = (
    "slide",
    "layout_id",
    "layout",
    "table",
    "table_range",
    "chart_id",
    "headline",
    "frame",
    "query_ref",
    "mart",
    "rows",
    "slide_url",
)


def _shorten(text: str, limit: int) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def _text_box_requests(
    object_id: str, rect: Rect, page_id: str, text: str, *, size: int, bold: bool = False
) -> list[dict[str, Any]]:
    return [
        {
            "createShape": {
                "objectId": object_id,
                "shapeType": "TEXT_BOX",
                "elementProperties": rect.element_properties(page_id),
            }
        },
        {"insertText": {"objectId": object_id, "text": text}},
        {
            "updateTextStyle": {
                "objectId": object_id,
                "style": {"fontSize": {"magnitude": size, "unit": "PT"}, "bold": bold},
                "fields": "fontSize,bold",
            }
        },
    ]


# A Slides table row's height scales with its font size, calibrated from two
# measured points: ~0.38in/row at the old 11pt default (kept exactly, so every
# pre-existing caller and test is unaffected) and ~0.28in/row at L9's 9pt
# Sources & SQL table (item 4/5). Linear between and beyond those two anchors —
# not a formula from font metrics, because what matters is matching the two
# rectangles this was actually measured against.
_TABLE_ROW_IN_BASE_PT = 9
_TABLE_ROW_IN_BASE = 0.28  # at _TABLE_ROW_IN_BASE_PT
_TABLE_ROW_IN_PER_PT = 0.05  # calibrated so 11pt reproduces the original 0.38in/row
MAX_TABLE_ROWS = 10
DEFAULT_TABLE_FONT_PT = 11
# Per-layout on-slide table font size (item 5): the 14pt default is what
# overflowed both L6 and L9 in the reviewed deck.
TABLE_FONT_PT: dict[str, int] = {"L6": 10, "L9": 9}


def table_row_height_in(font_pt: int) -> float:
    return _TABLE_ROW_IN_BASE + (font_pt - _TABLE_ROW_IN_BASE_PT) * _TABLE_ROW_IN_PER_PT


def rows_that_fit(height_in: float, *, font_pt: int = DEFAULT_TABLE_FONT_PT) -> int:
    return max(3, min(MAX_TABLE_ROWS, int(height_in / table_row_height_in(font_pt)) - 1))


def _table_requests(
    object_id: str,
    rect: Rect,
    page_id: str,
    columns: list[str],
    rows: list[list[Any]],
    *,
    font_pt: int = DEFAULT_TABLE_FONT_PT,
) -> list[dict[str, Any]]:
    """A native Slides table at an explicit rectangle.

    There is no request that *fills* a TABLE placeholder — and a library slide's
    table slot is an ordinary shape, not a placeholder — so both builds create
    the table at the slot's geometry and the caller deletes the shape it stood
    in for.

    Rows are capped by the slot's HEIGHT, not by a constant: a Slides table row
    has a minimum height, so a table with more rows than the slot can hold does
    not shrink — it grows downward over the footer. The cap is what keeps a
    generated slide inside its own layout.

    Every cell gets an explicit ``fontSize`` (item 5) — the Slides API's 14pt
    default is what overflowed both L6 and L9 in the reviewed deck — via one
    ``updateTextStyle`` per cell: a table cell's style is only addressable by
    ``cellLocation``, so there is no "whole table" request to send instead.
    """
    body = rows[: rows_that_fit(rect.h, font_pt=font_pt)]
    reqs: list[dict[str, Any]] = [
        {
            "createTable": {
                "objectId": object_id,
                "elementProperties": rect.element_properties(page_id),
                "rows": len(body) + 1,
                "columns": len(columns),
            }
        }
    ]

    def _style(row_index: int, col_index: int, *, bold: bool = False) -> dict[str, Any]:
        style: dict[str, Any] = {"fontSize": {"magnitude": font_pt, "unit": "PT"}}
        fields = "fontSize"
        if bold:
            style["bold"] = True
            fields = "fontSize,bold"
        return {
            "updateTextStyle": {
                "objectId": object_id,
                "cellLocation": {"rowIndex": row_index, "columnIndex": col_index},
                "textRange": {"type": "ALL"},
                "style": style,
                "fields": fields,
            }
        }

    for c, name in enumerate(columns):
        reqs.append(
            {
                "insertText": {
                    "objectId": object_id,
                    "cellLocation": {"rowIndex": 0, "columnIndex": c},
                    "text": str(name),
                }
            }
        )
        reqs.append(_style(0, c, bold=True))
    for r, row in enumerate(body, start=1):
        for c, value in enumerate(row):
            text = "" if value is None else str(value)
            if not text:
                # An empty cell needs neither request: `insertText` with "" is a
                # no-op, and the API 400s on `updateTextStyle` over a text-empty
                # cell ("The object (...) has no text") — this is exactly what
                # broke the L9 Sources & SQL slide's own pointer row, which has
                # blank cells by design (§ _append_sources). Skipping both is
                # equivalent to Slides' own default-styled blank cell.
                continue
            reqs.append(
                {
                    "insertText": {
                        "objectId": object_id,
                        "cellLocation": {"rowIndex": r, "columnIndex": c},
                        "text": text,
                    }
                }
            )
            reqs.append(_style(r, c))
    return reqs
