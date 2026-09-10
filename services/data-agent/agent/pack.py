"""Template packs — the curated catalogue as a reviewable file (s48).

A pack is two Google Workspace files a curator edits by hand plus a JSON
snapshot in the repo (``docs/template-packs.md`` is the contract):

  * ``Pack.slides`` — one **library slide** per layout. The builder duplicates a
    library slide and fills its slots, rather than creating a slide from a master
    layout: the Slides API cannot author master layouts, and the Slides editor
    cannot author CHART/TABLE placeholders, so a library slide is the only
    artifact that is authorable from *both* sides.
  * ``Pack.sheet`` — the ``_pack`` catalogue tab plus one ``tpl_*`` tab per table
    template, each carrying a hand-styled chart whose ChartSpec the builder
    clones. Styling a chart is a designer's job and the API's chart vocabulary is
    enormous; cloning a spec the curator made is how the deck inherits taste
    without this module ever growing a theme.

This module is the *read* side: parse ``packs/<name>/pack.json`` and turn it into
the ``deck.Layout`` catalogue the agent chooses from. ``scripts/pack_sync.py``
is the write side, and nothing else may write pack.json — it is a snapshot of
Google, not a source of truth.

An enabled layout that failed validation (unknown ref, no headline slot, a
chart_template that is not in the Sheet) is treated as **disabled**: a pack in a
half-edited state degrades to a smaller menu rather than to broken slides.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .deck import Layout, Rect, Slot

# rect = [x, y, w, h] in inches, as written by pack_sync.
_RECT_LEN = 4


@dataclass(frozen=True)
class SlotSpec:
    """One slot as pack.json records it."""

    name: str
    object_id: str
    rect: tuple[float, float, float, float]

    def to_slot(self) -> Slot:
        x, y, w, h = self.rect
        return Slot(name=self.name, object_id=self.object_id, rect=Rect(x, y, w, h))


@dataclass(frozen=True)
class LayoutSpec:
    """One ``_pack`` row, resolved against the two Google files."""

    id: str
    name: str
    enabled: bool
    use_when: str
    source: str = "slide"
    ref: str = ""
    slide_object_id: str = ""
    slots: dict[str, SlotSpec] = field(default_factory=dict)
    table_template: str = ""
    chart_template: dict[str, Any] | None = None
    series_max: int = 3
    grader_shape: str = ""
    issues: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        return self.enabled and not self.issues


@dataclass(frozen=True)
class PackSpec:
    """A whole pack.json."""

    name: str
    version: int
    slides_id: str
    sheet_id: str
    folder_id: str = ""
    synced_at: str = ""
    page: dict[str, float] = field(default_factory=dict)
    layouts: tuple[LayoutSpec, ...] = ()
    table_templates: dict[str, dict[str, Any]] = field(default_factory=dict)
    issues: tuple[str, ...] = ()

    def layout(self, layout_id: str) -> LayoutSpec | None:
        for spec in self.layouts:
            if spec.id == layout_id:
                return spec
        return None


def pack_path(pack_dir: str | Path, name: str) -> Path:
    return Path(pack_dir) / name / "pack.json"


def load_pack(path: str | Path) -> PackSpec | None:
    """Parse a pack.json, or None when it is absent or unreadable.

    Never raises: a malformed pack must fall back to the built-in catalogue and
    still answer the question, exactly as a missing one does. The failure is
    printed rather than swallowed silently so a broken sync is visible in logs.
    """
    p = Path(path)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[data-agent] pack {p} unreadable ({exc}); using the built-in catalogue")
        return None
    try:
        return _parse_pack(raw)
    except (KeyError, TypeError, ValueError) as exc:
        print(f"[data-agent] pack {p} malformed ({exc}); using the built-in catalogue")
        return None


def _parse_pack(raw: dict[str, Any]) -> PackSpec:
    return PackSpec(
        name=str(raw.get("name") or ""),
        version=int(raw.get("version") or 1),
        slides_id=str(raw.get("slides_id") or ""),
        sheet_id=str(raw.get("sheet_id") or ""),
        folder_id=str(raw.get("folder_id") or ""),
        synced_at=str(raw.get("synced_at") or ""),
        page={k: float(v) for k, v in (raw.get("page") or {}).items()},
        layouts=tuple(_parse_layout(entry) for entry in raw.get("layouts") or []),
        table_templates={str(k): dict(v) for k, v in (raw.get("table_templates") or {}).items()},
        issues=tuple(str(i) for i in raw.get("issues") or []),
    )


def _parse_layout(raw: dict[str, Any]) -> LayoutSpec:
    chart_template = raw.get("chart_template")
    return LayoutSpec(
        id=str(raw.get("id") or ""),
        name=str(raw.get("name") or ""),
        enabled=bool(raw.get("enabled", True)),
        use_when=str(raw.get("use_when") or ""),
        source=str(raw.get("source") or "slide"),
        ref=str(raw.get("ref") or ""),
        slide_object_id=str(raw.get("slide_object_id") or ""),
        slots={name: _parse_slot(name, s) for name, s in (raw.get("slots") or {}).items()},
        table_template=str(raw.get("table_template") or ""),
        chart_template=dict(chart_template) if isinstance(chart_template, dict) else None,
        series_max=int(raw.get("series_max") or 3),
        grader_shape=str(raw.get("grader_shape") or ""),
        issues=tuple(str(i) for i in raw.get("issues") or []),
    )


def _parse_slot(name: str, raw: dict[str, Any]) -> SlotSpec:
    rect = [float(v) for v in (raw.get("rect") or [])]
    if len(rect) != _RECT_LEN:
        raise ValueError(f"slot {name!r} needs a 4-number rect, got {raw.get('rect')!r}")
    return SlotSpec(
        name=name,
        object_id=str(raw.get("object_id") or ""),
        rect=(
            rect[0],
            rect[1],
            rect[2],
            rect[3],
        ),
    )


def pack_to_catalogue(pack: PackSpec) -> tuple[Layout, ...]:
    """The pack as the catalogue ``add_slide`` resolves names against.

    Every layout is included, enabled or not, so the builder can still *explain*
    a disabled layout; ``Layout.enabled`` is what actually withholds it from both
    ``layouts.md`` and name resolution. A layout with validation issues comes
    back disabled — see the module docstring.
    """
    layouts: list[Layout] = []
    for spec in pack.layouts:
        slots = {name: s.to_slot() for name, s in spec.slots.items()}
        layouts.append(
            Layout(
                name=spec.name,
                use_when=spec.use_when,
                # A `source: layout` row addresses a master layout by objectId,
                # which is exactly what the s46 code path already knows how to
                # create a slide from.
                layout_id=spec.slide_object_id if spec.source == "layout" else None,
                chart=slots["chart"].rect if "chart" in slots else None,
                table=slots["table"].rect if "table" in slots else None,
                commentary=slots["commentary"].rect if "commentary" in slots else None,
                kpi=slots["kpi"].rect if "kpi" in slots else None,
                enabled=spec.usable,
                id=spec.id,
                source=spec.source,
                slide_object_id=spec.slide_object_id if spec.source == "slide" else "",
                slots=slots,
                table_template=spec.table_template,
                chart_template=spec.chart_template,
                series_max=spec.series_max,
                grader_shape=spec.grader_shape,
            )
        )
    return tuple(layouts)
