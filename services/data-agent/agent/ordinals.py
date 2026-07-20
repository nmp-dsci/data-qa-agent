"""Ordinal category ordering (s23) — the single source of truth for band order.

Some dimension columns are *ordinal* (a natural order) but stored as strings that
sort alphabetically wrong: ``area_band`` puts ``'1000-5000'`` before ``'400-700'``,
``bedroom_band`` puts ``'10'`` before ``'2'``. This module gives every chart a
deterministic order so the axis reads ``<400 › 400-700 › … › 5000+ › unknown``.

Where it's applied: :func:`agent.pages.chart_object_from_spec` sorts the lifted
chart rows by the dimension's ordinal order, so ALL surfaces (chat, Explore,
golden) render bands correctly with no per-answer work.

Two layers, in priority order:

* a curator-editable **DB override** (``app.dataset_ordinals``, keyed by
  ``(dataset_id, column)``) loaded into :data:`_OVERRIDES` by :func:`load_overrides`
  (the async endpoints call it before lifting; edits take effect on the next Run);
* a code **seed** :data:`BAND_ORDERS` (this file) — the fallback defaults.

Keyed by ``(dataset, column)`` because the SAME column name can mean different
things in different datasets; when the caller doesn't know the dataset we fall
back to a column-only match (safe while the ordinal columns are dataset-disjoint).
``unknown`` / unrecognised values always sort last, never dropped.
"""

from __future__ import annotations

import time
from typing import Any

# ---------------------------------------------------------------------------
# Seed — the code-level defaults, keyed by (dataset slug, column).
# ---------------------------------------------------------------------------
BAND_ORDERS: dict[tuple[str, str], list[str]] = {
    ("nsw_sales", "area_band"): ["<400", "400-700", "700-1000", "1000-5000", "5000+", "unknown"],
    ("nsw_rent", "bedroom_band"): ["0", "1", "2", "3", "4", "5+", "unknown"],
}

# ---------------------------------------------------------------------------
# Override cache — app.dataset_ordinals, refreshed with a short TTL so a curator
# edit is picked up on the next Run without a redeploy.
# ---------------------------------------------------------------------------
_OVERRIDES: dict[tuple[str, str], list[str]] | None = None
_loaded_at: float = 0.0
_TTL_SECONDS = 5.0


async def load_overrides(*, ttl: float = _TTL_SECONDS) -> None:
    """Refresh the curator-override cache from ``app.dataset_ordinals`` (best effort).

    Called by the async endpoints before a lift. Any failure (grants/RLS/missing
    table) degrades silently to the code seed — the order still applies.
    """
    global _OVERRIDES, _loaded_at
    now = time.monotonic()
    if _OVERRIDES is not None and (now - _loaded_at) < ttl:
        return
    cache: dict[tuple[str, str], list[str]] = {}
    try:
        from sqlalchemy import text

        from .db import engine

        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT d.slug, o.column_name, o.ordered_values "
                        "FROM app.dataset_ordinals o JOIN app.datasets d ON d.id = o.dataset_id"
                    )
                )
            ).all()
        for slug, col, vals in rows:
            if isinstance(vals, list) and vals:
                cache[(str(slug), str(col))] = [str(v) for v in vals]
        _OVERRIDES = cache
    except Exception:  # noqa: BLE001 — override is best-effort; seed is the fallback
        if _OVERRIDES is None:
            _OVERRIDES = {}
    _loaded_at = now


def _column_only(store: dict[tuple[str, str], list[str]], col: str) -> list[str] | None:
    for (_ds, c), order in store.items():
        if c == col:
            return order
    return None


def resolve_order(dataset: str | None, col: str) -> list[str] | None:
    """The canonical order for ``col`` in ``dataset`` — override first, then seed.

    Exact ``(dataset, col)`` wins; when ``dataset`` is unknown (or has no entry)
    we fall back to a column-only match so the order still applies on surfaces
    that don't thread the dataset through.
    """
    overrides = _OVERRIDES or {}
    if dataset:
        if (dataset, col) in overrides:
            return overrides[(dataset, col)]
        if (dataset, col) in BAND_ORDERS:
            return BAND_ORDERS[(dataset, col)]
    return _column_only(overrides, col) or _column_only(BAND_ORDERS, col)


def order_rows(
    rows: list[dict[str, Any]], col: str, dataset: str | None = None
) -> list[dict[str, Any]]:
    """Return ``rows`` sorted by the ordinal order of ``col`` (stable; unknown last).

    A no-op when ``col`` isn't a known ordinal or isn't present — so it never
    disturbs a column that genuinely wants its incoming order (e.g. a time axis).
    The sort is stable, so a grouped/series sub-order is preserved within each band.
    """
    if not rows or not col:
        return rows
    order = resolve_order(dataset, col)
    if not order:
        return rows
    if not any(col in r for r in rows):
        return rows
    rank = {v: i for i, v in enumerate(order)}
    last = len(order)
    return sorted(rows, key=lambda r: rank.get(str(r.get(col)), last))
