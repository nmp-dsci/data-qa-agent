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

from dataclasses import dataclass, field
from typing import Any

from .gsuite import EMU_PER_INCH, GoogleClient, deck_embed_url, deck_url, sheet_url
from .units import CURRENCY, PERCENT, unit_for_column


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
class Layout:
    """One entry in the catalogue the agent chooses from."""

    name: str
    use_when: str
    predefined: str | None = None  # e.g. "TITLE_ONLY" when there is no pack
    layout_id: str | None = None  # objectId, when the pack supplies it
    chart: Rect | None = None
    table: Rect | None = None
    commentary: Rect | None = None
    kpi: Rect | None = None
    enabled: bool = True

    @property
    def wants_chart(self) -> bool:
        return self.chart is not None

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
        slots = []
        if layout.chart is not None:
            slots.append("chart")
        if layout.table is not None:
            slots.append("table")
        if layout.kpi is not None:
            slots.append("kpi")
        if layout.commentary is not None:
            slots.append("commentary")
        lines.append(f"## {layout.name}")
        lines.append("")
        lines.append(layout.use_when)
        lines.append("")
        lines.append(f"Accepts: headline{', ' + ', '.join(slots) if slots else ''}")
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
        }


@dataclass
class DeckBuilder:
    """Creates one Sheet + one deck per run and appends slides to them."""

    client: GoogleClient
    catalogue: tuple[Layout, ...]
    title: str
    presentation_id: str = ""
    spreadsheet_id: str = ""
    template_id: str = ""
    slides: list[SlideRecord] = field(default_factory=list)
    _tab_names: set[str] = field(default_factory=set)

    def layout(self, name: str) -> Layout | None:
        wanted = name.strip().casefold()
        for entry in self.catalogue:
            if entry.enabled and entry.name.casefold() == wanted:
                return entry
        return None

    def layout_names(self) -> list[str]:
        return [entry.name for entry in self.catalogue if entry.enabled]

    async def start(self, template_id: str) -> None:
        """Create the backing Sheet and the deck, then clear the deck's default slide."""
        self.template_id = template_id
        self.spreadsheet_id, _ = await self.client.create_spreadsheet(f"{self.title} — data")
        if template_id:
            self.presentation_id = await self.client.copy_file(template_id, self.title)
        else:
            self.presentation_id = await self.client.create_presentation(self.title)
        doc = await self.client.get_presentation(self.presentation_id)
        existing = [str(s.get("objectId")) for s in doc.get("slides", [])]
        if existing:
            await self.client.slides_batch(
                self.presentation_id,
                [{"deleteObject": {"objectId": oid}} for oid in existing],
            )

    async def _write_frame(self, tab: str, columns: list[str], rows: list[list[Any]]) -> int:
        """Write a frame to its own tab and format its numeric columns."""
        sheet_id = await self.client.add_sheet(self.spreadsheet_id, tab)
        await self.client.write_values(self.spreadsheet_id, f"'{tab}'!A1", [list(columns), *rows])
        formats: list[dict[str, Any]] = [
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": 1,
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
            formats.append(
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 1,
                            "startColumnIndex": idx,
                            "endColumnIndex": idx + 1,
                        },
                        "cell": {"userEnteredFormat": {"numberFormat": fmt}},
                        "fields": "userEnteredFormat.numberFormat",
                    }
                }
            )
        await self.client.sheets_batch(self.spreadsheet_id, formats)
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

        def source(start_col: int, end_col: int) -> dict[str, Any]:
            return {
                "sources": [
                    {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": row_count + 1,  # +1 for the header row
                        "startColumnIndex": start_col,
                        "endColumnIndex": end_col,
                    }
                ]
            }

        series = [
            {"series": {"sourceRange": source(i, i + 1)}, "targetAxis": "LEFT_AXIS"}
            for i in range(1, col_count)
        ]
        spec = {
            "title": title,
            "basicChart": {
                "chartType": chart_type,
                "legendPosition": "BOTTOM_LEGEND" if len(series) > 1 else "NO_LEGEND",
                "headerCount": 1,
                "domains": [{"domain": {"sourceRange": source(0, 1)}}],
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

    async def add_slide(
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
        """Build one slide as a single ordered, atomic Slides batch.

        Sheet writes happen first because ``createSheetsChart`` needs a chartId
        that already exists. Everything touching the slide itself — creating it
        from the layout, filling its title, placing the commentary, embedding the
        chart or drawing the table — goes in one batch, so a slide either lands
        whole or not at all.
        """
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
                        # LINKED, not NOT_LINKED_IMAGE: the chart stays tied to the
                        # Sheet and can be refreshed rather than rebuilt. It still
                        # renders for a viewer with no access to the Sheet, because
                        # it is a cached server-side render, not a live fetch.
                        "linkingMode": "LINKED",
                        "elementProperties": layout.chart.element_properties(slide_id),  # type: ignore[union-attr]
                    }
                }
            )
        elif columns and rows and layout.table is not None:
            sheet_tab = self._unique_tab(tab_name or f"slide{index + 1}")
            row_count = len(rows)
            await self._write_frame(sheet_tab, columns, rows)
            requests.extend(_table_requests(f"table_{index}", layout, slide_id, columns, rows))

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
        )
        self.slides.append(record)
        return record

    def _unique_tab(self, base: str) -> str:
        name = base[:80] or "data"
        n = 2
        while name in self._tab_names:
            name = f"{base[:76]}_{n}"
            n += 1
        self._tab_names.add(name)
        return name

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
            "slides": [
                {
                    "index": s.index,
                    "layout": s.layout,
                    "headline": s.headline,
                    "has_chart": s.has_chart,
                    "has_table": s.has_table,
                    "has_kpi": s.has_kpi,
                    "sheet_tab": s.sheet_tab,
                    "rows": s.rows,
                    # The template picked and every option passed. Nested rather
                    # than flattened so the keys G5 asserts on stay a small,
                    # stable surface while this can grow freely.
                    "spec": s.spec(),
                }
                for s in self.slides
            ],
            # Which layouts this deck used, in order — the one-line answer to
            # "what did the agent choose?" without walking every slide.
            "layouts_used": [s.layout for s in self.slides],
            # The menu it chose from. A deck is only interpretable against the
            # catalogue that was live when it was built: a slide that looks like
            # a poor choice may have been the only layout enabled at the time.
            "catalogue": self.layout_names(),
            "template_pack": self.template_id or "built-in catalogue",
        }


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


def _table_requests(
    object_id: str,
    layout: Layout,
    page_id: str,
    columns: list[str],
    rows: list[list[Any]],
) -> list[dict[str, Any]]:
    """A native Slides table. Like charts, there is no request that fills a TABLE
    placeholder, so it is created at the layout's cached geometry."""
    body = rows[:10]
    rect = layout.table
    assert rect is not None  # guarded by the caller
    reqs: list[dict[str, Any]] = [
        {
            "createTable": {
                "objectId": object_id,
                "elementProperties": {"pageObjectId": page_id},
                "rows": len(body) + 1,
                "columns": len(columns),
            }
        }
    ]
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
    for r, row in enumerate(body, start=1):
        for c, value in enumerate(row):
            reqs.append(
                {
                    "insertText": {
                        "objectId": object_id,
                        "cellLocation": {"rowIndex": r, "columnIndex": c},
                        "text": "" if value is None else str(value),
                    }
                }
            )
    return reqs
