"""The unit vocabulary — what a number IS, resolved from the metric behind it.

The bug these guard: a number was formatted by guessing at its column NAME, so
a trend (whose value column is literally ``value``) was drawn in dollars and a
YoY on rent kept claiming dollars after the derive turned it into a percentage.
"""

from __future__ import annotations

from agent.units import unit_for_column, unit_for_measure


def test_known_metrics_take_their_declared_unit() -> None:
    assert unit_for_column("avg_weekly_rent") == "currency"
    assert unit_for_column("total_sale_value") == "currency"
    assert unit_for_column("n_rented") == "number"
    assert unit_for_column("gross_yield_pct") == "percent"


def test_an_unknown_name_is_a_plain_number_not_money() -> None:
    """The old heuristic read "value"/"rent" anywhere in a name as currency, so
    every trend frame — bond counts included — got a dollar sign. Nothing is
    known about these, and a bare count is the honest reading."""
    for name in ("value", "_v", "segment", "delta", "score"):
        assert unit_for_column(name) == "number", name


def test_a_derive_renames_the_unit_as_well_as_the_column() -> None:
    assert unit_for_column("avg_weekly_rent growth") == "percent"
    assert unit_for_column("avg_weekly_rent yoy") == "percent"
    assert unit_for_column("n_rented share") == "percent"
    # cumulative / rolling / latest are still the measure itself.
    assert unit_for_column("total_weekly_rent cumulative") == "currency"
    assert unit_for_column("avg_sale_price latest") == "currency"


def test_pivot_column_names_resolve_through_their_metric() -> None:
    """A cross-tab column is "<metric> · <data value>" — the value part says
    nothing, and the Δ% gap is a percentage whatever the metric was."""
    assert unit_for_column("avg_weekly_rent · 2077") == "currency"
    assert unit_for_column("n_rented · 2077") == "number"
    assert unit_for_column("avg_weekly_rent · Δ") == "currency"
    assert unit_for_column("avg_weekly_rent · Δ%") == "percent"


def test_a_weighted_average_keeps_the_numerator_unit() -> None:
    """$ per bond is still dollars; $ per $ is a bare ratio."""
    rent = {"base": "wavg", "num": "total_weekly_rent", "den": "n_rented", "derive": ""}
    assert unit_for_measure(rent) == "currency"
    ratio = {"base": "wavg", "num": "total_weekly_rent", "den": "total_sale_value", "derive": ""}
    assert unit_for_measure(ratio) == "number"


def test_the_derive_wins_over_the_base_measure() -> None:
    """The user-visible defect: a YoY on avg weekly rent is a percentage, and
    both the axis and the tooltip were annotating it as money."""
    base = {"base": "wavg", "num": "total_weekly_rent", "den": "n_rented"}
    assert unit_for_measure({**base, "derive": "yoy"}) == "percent"
    assert unit_for_measure({**base, "derive": "growth"}) == "percent"
    assert unit_for_measure({**base, "derive": "index"}) == "number"
    # A window that only picks a value keeps the measure's own unit.
    assert unit_for_measure({**base, "derive": "latest"}) == "currency"
    assert unit_for_measure({"base": "sum", "source": "n_sold", "derive": "share"}) == "percent"


def test_names_outside_the_manifest_are_read_as_whole_words() -> None:
    """Ad-hoc columns (a SQL-editor chart, the ops deck) have no manifest entry
    but still say what they are. Whole words, not substrings: the old heuristic
    matched /rent/ inside `n_rented`, a count, and called it money."""
    assert unit_for_column("median_price") == "currency"
    assert unit_for_column("total_sale_value") == "currency"
    assert unit_for_column("pass_pct") == "percent"
    assert unit_for_column("% unit") == "percent"
    assert unit_for_column("rented_homes") == "number"
    assert unit_for_column("runs") == "number"
    # A lone "value" is a placeholder column name, not a claim about money —
    # the exact bug: every trend frame's column is called "value".
    assert unit_for_column("value") == "number"
    assert unit_for_column("measure") == "number"
