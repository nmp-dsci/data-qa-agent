"""Ordinal category ordering (s23) — bands sort by their natural order, not A-Z."""

from __future__ import annotations

from agent.ordinals import order_rows, resolve_order
from agent.pages import chart_object_from_spec

_ALPHA = [
    {"area_band": "1000-5000", "suburb": "Hornsby", "v": 1},
    {"area_band": "400-700", "suburb": "Hornsby", "v": 2},
    {"area_band": "<400", "suburb": "Hornsby", "v": 3},
    {"area_band": "700-1000", "suburb": "Hornsby", "v": 4},
    {"area_band": "unknown", "suburb": "Hornsby", "v": 5},
    {"area_band": "5000+", "suburb": "Hornsby", "v": 6},
]


def test_resolve_order_seed_and_column_only_fallback() -> None:
    sales_order = resolve_order("nsw_sales", "area_band")
    assert sales_order is not None and sales_order[0] == "<400"
    # dataset unknown → column-only fallback still finds the seed order.
    assert resolve_order(None, "area_band") == resolve_order("nsw_sales", "area_band")
    rent_order = resolve_order("nsw_rent", "bedroom_band")
    assert rent_order is not None and rent_order[:2] == ["0", "1"]
    # a non-ordinal column has no order.
    assert resolve_order("nsw_sales", "suburb") is None


def test_order_rows_sorts_bands_ordinally_unknown_last() -> None:
    out = [r["area_band"] for r in order_rows(_ALPHA, "area_band", "nsw_sales")]
    assert out == ["<400", "400-700", "700-1000", "1000-5000", "5000+", "unknown"]


def test_order_rows_is_noop_for_non_ordinal_or_missing_column() -> None:
    assert order_rows(_ALPHA, "suburb", "nsw_sales") == _ALPHA  # not ordinal
    assert order_rows(_ALPHA, "month", "nsw_sales") == _ALPHA  # absent → unchanged


def test_order_rows_is_stable_within_a_band_for_grouped_series() -> None:
    rows = [
        {"area_band": "400-700", "suburb": "Hornsby"},
        {"area_band": "<400", "suburb": "Hornsby"},
        {"area_band": "400-700", "suburb": "Normanhurst"},
        {"area_band": "<400", "suburb": "Normanhurst"},
    ]
    out = order_rows(rows, "area_band", "nsw_sales")
    # <400 bands first (both suburbs, original sub-order kept), then 400-700.
    assert [(r["area_band"], r["suburb"]) for r in out] == [
        ("<400", "Hornsby"),
        ("<400", "Normanhurst"),
        ("400-700", "Hornsby"),
        ("400-700", "Normanhurst"),
    ]


def test_chart_object_from_spec_orders_ordinal_x_axis() -> None:
    """The lift is where it lands — a bar spec over area_band comes out ordinal
    (the combo/single-mark paths all read the same ordered rows)."""
    spec = {
        "mark": "bar",
        "encoding": {
            "x": {"field": "area_band", "type": "nominal"},
            "y": {"field": "v", "type": "quantitative"},
        },
        "data": {"values": _ALPHA},
    }
    obj = chart_object_from_spec(spec, element_id="obj:x", role="chart", dataset="nsw_sales")
    assert obj is not None
    bands: list[str] = []
    for row in obj.data["rows"]:
        b = row["area_band"]
        if b not in bands:
            bands.append(b)
    assert bands == ["<400", "400-700", "700-1000", "1000-5000", "5000+", "unknown"]
