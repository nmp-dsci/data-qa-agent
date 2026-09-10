"""Handover — what happened to a deck after we handed it over (s48 §6/§7).

Pure, offline transforms. The Drive-facing side (taking the snapshot, reading
it back from Postgres, deciding when to poll) lives in
``scripts/handover_poll.py`` and ``agent/handover_snapshot.py`` instead —
everything here needs only plain dicts, so it is unit-testable without a
Google credential or a database:

* ``normalise_deck`` is ``agent.handover_snapshot.normalise_deck`` re-exported
  from here — the builder's version-1 baseline and the poller's later snapshot
  go through the exact same function, so a deck field can never mean one thing
  in the baseline and another in the diff. Import it from either module.
* ``normalise_sheet`` turns the poller's raw tab values into the same
  ``{"tables": [{name, header, rows}]}`` shape ``handover_snapshot.snapshot_sheet``
  builds from the manifest's named ranges directly — two data sources
  (a whole tab vs. one range at a time), one shape.
* ``diff_snapshots`` compares two normalised snapshots of the same kind (plus
  the run's manifest, for the ``layout_id``/``table_name`` context the raw
  snapshot doesn't carry) and emits the ``app.artifact_edits`` event rows.
"""

from __future__ import annotations

import re
from typing import Any

from .handover_snapshot import normalise_deck

__all__ = ["diff_snapshots", "normalise_deck", "normalise_sheet"]

_EVENTS = (
    "headline_changed",
    "commentary_changed",
    "notes_changed",
    "kpi_changed",
    "chart_type_changed",
    "chart_deleted",
    "chart_added",
    "slide_added",
    "slide_deleted",
    "slide_reordered",
    "table_values_changed",
    "table_cell_changed",
    "table_rows_added",
    "table_rows_deleted",
    "file_renamed",
)

# Text slots whose content is a fact a human might correct in place (a KPI
# figure retyped, its label reworded) — worth a distinct event from the
# catch-all slide text, unlike subtitle/footer/source which are builder chrome.
_KPI_TEXT_KEYS = ("kpi", "kpi_label")

_A1_RANGE = re.compile(
    r"^(?:'?(?P<tab>[^'!]+)'?!)?(?P<c1>[A-Z]+)(?P<r1>\d+)(?::(?P<c2>[A-Z]+)(?P<r2>\d+))?$"
)


# ---------------------------------------------------------------------------
# normalise_sheet
# ---------------------------------------------------------------------------


def _parse_a1(a1_range: str) -> tuple[str | None, int, int, int | None, int | None]:
    """``"Data!A5:D20"`` -> (tab, row0, col0, row1_exclusive, col1_exclusive).

    Row/col are 0-based; the end bounds are ``None`` when the range is open
    (a single cell, or a range with no end column/row) — the caller then
    slices to the end of whatever data it has.
    """
    match = _A1_RANGE.match(a1_range.strip())
    if not match:
        return None, 0, 0, None, None
    tab = match.group("tab")
    c1 = _col_to_index(match.group("c1"))
    r1 = int(match.group("r1")) - 1
    c2_raw, r2_raw = match.group("c2"), match.group("r2")
    c2 = _col_to_index(c2_raw) + 1 if c2_raw else None
    r2 = int(r2_raw) if r2_raw else None
    return tab, r1, c1, r2, c2


def _col_to_index(letters: str) -> int:
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch.upper()) - ord("A") + 1)
    return idx - 1


def normalise_sheet(
    values_by_tab: dict[str, list[list[Any]]], manifest: dict[str, Any]
) -> dict[str, Any]:
    """The run's ``Data``/overflow tabs -> ``{tables: [{name, header, rows}]}``.

    Slices each table out of ``values_by_tab`` using the manifest's
    ``table_range`` per slide (§6), so a snapshot only carries the rows a
    table actually owns rather than a whole tab's contents. A slide with no
    ``table_name``/``table_range`` (a chart-only or text-only layout)
    contributes nothing.
    """
    tables: list[dict[str, Any]] = []
    for slide in manifest.get("slides") or []:
        name = slide.get("table_name")
        table_range = slide.get("table_range")
        if not name or not table_range:
            continue
        tab, r0, c0, r1, c1 = _parse_a1(table_range)
        rows_2d = values_by_tab.get(tab) if tab else None
        if rows_2d is None and len(values_by_tab) == 1:
            # A single-tab snapshot (the common case): the caller didn't need
            # to key by tab name because there was only one.
            rows_2d = next(iter(values_by_tab.values()), [])
        rows_2d = rows_2d or []
        window = rows_2d[r0:r1] if r1 is not None else rows_2d[r0:]
        window = [row[c0:c1] if c1 is not None else row[c0:] for row in window]
        header = window[0] if window else []
        body = window[1:] if window else []
        tables.append({"name": name, "header": header, "rows": body})
    return {"tables": tables}


# ---------------------------------------------------------------------------
# diff_snapshots
# ---------------------------------------------------------------------------


def _manifest_slide_by_object_id(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        s.get("slide_object_id"): s
        for s in (manifest.get("slides") or [])
        if s.get("slide_object_id")
    }


def _manifest_slide_by_table_name(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s.get("table_name"): s for s in (manifest.get("slides") or []) if s.get("table_name")}


def _event(
    event: str,
    *,
    layout_id: str | None = None,
    slide_index: int | None = None,
    slide_object_id: str | None = None,
    table_name: str | None = None,
    before: Any = None,
    after: Any = None,
) -> dict[str, Any]:
    return {
        "event": event,
        "layout_id": layout_id,
        "slide_index": slide_index,
        "slide_object_id": slide_object_id,
        "table_name": table_name,
        "before": before,
        "after": after,
    }


def _diff_deck(
    before: dict[str, Any],
    after: dict[str, Any],
    by_object_id: dict[str, dict[str, Any]],
    charts_before: dict[Any, dict[str, Any]] | None,
    charts_after: dict[Any, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    before_slides = {
        s["slide_object_id"]: s for s in before.get("slides") or [] if s.get("slide_object_id")
    }
    after_slides = {
        s["slide_object_id"]: s for s in after.get("slides") or [] if s.get("slide_object_id")
    }

    for object_id, slide in after_slides.items():
        manifest_slide = by_object_id.get(object_id, {})
        layout_id = manifest_slide.get("layout_id")

        if object_id not in before_slides:
            events.append(
                _event(
                    "slide_added",
                    layout_id=layout_id,
                    slide_index=slide.get("index"),
                    slide_object_id=object_id,
                    after=slide,
                )
            )
            continue

        prior = before_slides[object_id]
        if prior.get("index") != slide.get("index"):
            events.append(
                _event(
                    "slide_reordered",
                    layout_id=layout_id,
                    slide_index=slide.get("index"),
                    slide_object_id=object_id,
                    before=prior.get("index"),
                    after=slide.get("index"),
                )
            )
        if prior.get("headline") != slide.get("headline"):
            events.append(
                _event(
                    "headline_changed",
                    layout_id=layout_id,
                    slide_index=slide.get("index"),
                    slide_object_id=object_id,
                    before=prior.get("headline"),
                    after=slide.get("headline"),
                )
            )
        if prior.get("commentary") != slide.get("commentary"):
            events.append(
                _event(
                    "commentary_changed",
                    layout_id=layout_id,
                    slide_index=slide.get("index"),
                    slide_object_id=object_id,
                    before=prior.get("commentary"),
                    after=slide.get("commentary"),
                )
            )
        if prior.get("notes") != slide.get("notes"):
            events.append(
                _event(
                    "notes_changed",
                    layout_id=layout_id,
                    slide_index=slide.get("index"),
                    slide_object_id=object_id,
                    before=prior.get("notes"),
                    after=slide.get("notes"),
                )
            )

        prior_texts = prior.get("texts") or {}
        after_texts = slide.get("texts") or {}
        for key in _KPI_TEXT_KEYS:
            if prior_texts.get(key, "") != after_texts.get(key, ""):
                events.append(
                    _event(
                        "kpi_changed",
                        layout_id=layout_id,
                        slide_index=slide.get("index"),
                        slide_object_id=object_id,
                        before={key: prior_texts.get(key, "")},
                        after={key: after_texts.get(key, "")},
                    )
                )

        events.extend(
            _diff_charts(
                prior.get("charts") or [],
                slide.get("charts") or [],
                layout_id=layout_id,
                slide_index=slide.get("index"),
                slide_object_id=object_id,
                charts_before=charts_before,
                charts_after=charts_after,
            )
        )

        prior_tables = {t.get("object_id"): t for t in prior.get("tables") or []}
        for table in slide.get("tables") or []:
            was = prior_tables.get(table.get("object_id"))
            if was is None:
                continue
            table_name = manifest_slide.get("table_name")
            if (was.get("rows") or 0) < (table.get("rows") or 0):
                events.append(
                    _event(
                        "table_rows_added",
                        layout_id=layout_id,
                        slide_index=slide.get("index"),
                        slide_object_id=object_id,
                        table_name=table_name,
                        before=was.get("rows"),
                        after=table.get("rows"),
                    )
                )
            elif (was.get("rows") or 0) > (table.get("rows") or 0):
                events.append(
                    _event(
                        "table_rows_deleted",
                        layout_id=layout_id,
                        slide_index=slide.get("index"),
                        slide_object_id=object_id,
                        table_name=table_name,
                        before=was.get("rows"),
                        after=table.get("rows"),
                    )
                )
            elif (was.get("cells") or []) != (table.get("cells") or []):
                events.append(
                    _event(
                        "table_cell_changed",
                        layout_id=layout_id,
                        slide_index=slide.get("index"),
                        slide_object_id=object_id,
                        table_name=table_name,
                        before=was.get("cells"),
                        after=table.get("cells"),
                    )
                )

    for object_id, slide in before_slides.items():
        if object_id in after_slides:
            continue
        manifest_slide = by_object_id.get(object_id, {})
        events.append(
            _event(
                "slide_deleted",
                layout_id=manifest_slide.get("layout_id"),
                slide_index=slide.get("index"),
                slide_object_id=object_id,
                before=slide,
            )
        )
    return events


def _diff_charts(
    before_charts: list[dict[str, Any]],
    after_charts: list[dict[str, Any]],
    *,
    layout_id: str | None,
    slide_index: int | None,
    slide_object_id: str | None,
    charts_before: dict[Any, dict[str, Any]] | None,
    charts_after: dict[Any, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    before_by_id = {c.get("chart_id"): c for c in before_charts}
    after_by_id = {c.get("chart_id"): c for c in after_charts}

    for chart_id, chart in after_by_id.items():
        if chart_id not in before_by_id:
            events.append(
                _event(
                    "chart_added",
                    layout_id=layout_id,
                    slide_index=slide_index,
                    slide_object_id=slide_object_id,
                    after=chart,
                )
            )
            continue
        if charts_before is not None and charts_after is not None:
            spec_before = (charts_before or {}).get(chart_id)
            spec_after = (charts_after or {}).get(chart_id)
            if (
                spec_before is not None
                and spec_after is not None
                and spec_before.get("chartType") != spec_after.get("chartType")
            ):
                events.append(
                    _event(
                        "chart_type_changed",
                        layout_id=layout_id,
                        slide_index=slide_index,
                        slide_object_id=slide_object_id,
                        before=spec_before.get("chartType"),
                        after=spec_after.get("chartType"),
                    )
                )

    for chart_id, chart in before_by_id.items():
        if chart_id not in after_by_id:
            events.append(
                _event(
                    "chart_deleted",
                    layout_id=layout_id,
                    slide_index=slide_index,
                    slide_object_id=slide_object_id,
                    before=chart,
                )
            )
    return events


def _diff_sheet(
    before: dict[str, Any],
    after: dict[str, Any],
    by_table_name: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    before_tables = {t["name"]: t for t in before.get("tables") or [] if t.get("name")}
    after_tables = {t["name"]: t for t in after.get("tables") or [] if t.get("name")}

    for name, table in after_tables.items():
        was = before_tables.get(name)
        if was is None:
            continue
        manifest_slide = by_table_name.get(name, {})
        layout_id = manifest_slide.get("layout_id")
        slide_index = manifest_slide.get("index")
        slide_object_id = manifest_slide.get("slide_object_id")

        was_rows, now_rows = len(was.get("rows") or []), len(table.get("rows") or [])
        if now_rows > was_rows:
            events.append(
                _event(
                    "table_rows_added",
                    layout_id=layout_id,
                    slide_index=slide_index,
                    slide_object_id=slide_object_id,
                    table_name=name,
                    before=was_rows,
                    after=now_rows,
                )
            )
        elif now_rows < was_rows:
            events.append(
                _event(
                    "table_rows_deleted",
                    layout_id=layout_id,
                    slide_index=slide_index,
                    slide_object_id=slide_object_id,
                    table_name=name,
                    before=was_rows,
                    after=now_rows,
                )
            )
        elif was.get("header") != table.get("header") or was.get("rows") != table.get("rows"):
            events.append(
                _event(
                    "table_values_changed",
                    layout_id=layout_id,
                    slide_index=slide_index,
                    slide_object_id=slide_object_id,
                    table_name=name,
                    before=was,
                    after=table,
                )
            )
    return events


def diff_snapshots(
    before: dict[str, Any],
    after: dict[str, Any],
    manifest: dict[str, Any],
    *,
    charts_before: dict[Any, dict[str, Any]] | None = None,
    charts_after: dict[Any, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Diff two normalised snapshots of the *same kind* (both deck or both
    sheet), emitting one dict per §7 event. ``manifest`` is the run's artifact
    manifest (deck.manifest()'s dict) — used only to attach ``layout_id``/
    ``table_name`` context, never diffed itself.
    """
    if "slides" in before or "slides" in after:
        return _diff_deck(
            before, after, _manifest_slide_by_object_id(manifest), charts_before, charts_after
        )
    return _diff_sheet(before, after, _manifest_slide_by_table_name(manifest))
