"""Value units — what a number IS, so every surface annotates it the same way.

A bare number is ambiguous: 82 can be $82, 82 bonds, or 82%. The renderer used
to guess from the field NAME (a regex over ``price|value|rent|…``), which is
wrong in two ways that bite constantly:

* every trend frame's value column is literally called ``value``, so *every*
  line chart — bond counts included — was labelled in dollars; and
* a derive changes the unit. A YoY on ``avg_weekly_rent`` is a percentage, but
  the name still says rent, so the axis and the tooltip both claimed dollars.

So the unit travels WITH the number. Each producer resolves it here — from the
metric the number was aggregated from, plus the derive applied on top — and
stamps it on the object it emits (``data.unit`` on a chart, ``format`` on a
table column). The renderer formats what it was told and never re-guesses.

``COLUMN_UNITS`` mirrors the Explore manifest's per-metric ``fmt``
(backend-api, ``app/explore/manifest.py``), which is the source of truth;
``tests/test_explore_agent_sync.py`` asserts the two agree. The frontend keeps
a fallback copy for objects saved before units existed
(``frontend/src/ui/charts/units.ts``), checked against this module by
``services/data-agent/tests/test_registry_sync.py``.
"""

from __future__ import annotations

import re
from typing import Any

CURRENCY = "currency"
NUMBER = "number"
PERCENT = "percent"

UNITS = (CURRENCY, NUMBER, PERCENT)

# Mart metric/column → unit. Mirrors the manifest's `fmt` per metric; the
# physical columns behind them (which the object builder aggregates directly)
# carry the same unit as the metric of the same name.
COLUMN_UNITS: dict[str, str] = {
    "n_sold": NUMBER,
    "n_rented": NUMBER,
    "n_unit": NUMBER,
    "total_sale_value": CURRENCY,
    "total_weekly_rent": CURRENCY,
    "avg_sale_price": CURRENCY,
    "avg_weekly_rent": CURRENCY,
    "gross_yield_pct": PERCENT,
    "pct_unit": PERCENT,
}

# A derive can REPLACE the base unit: a growth on dollars is a percentage, an
# index is a unitless 100-base, a rank is a position. The rest (latest, rolling,
# cumulative) are still the measure itself, so they inherit the base unit.
DERIVE_UNITS: dict[str, str] = {
    "growth": PERCENT,
    "yoy": PERCENT,
    "share": PERCENT,
    "index": NUMBER,
    "rank": NUMBER,
}

# A derived column is named "<metric> <derive>" (_series_derive_lines) and a
# pivot column "<metric> · <value>" / "<metric> · Δ%" — so a stored object whose
# unit predates this module still resolves through its column name.
_DERIVE_SUFFIX = re.compile(
    r"\s+(growth|yoy|share|index|rank|cumulative|rolling|latest)\s*%?$", re.IGNORECASE
)
_PIVOT_SEP = " · "

# Names outside the manifest still say what they are — an ad-hoc SQL-editor
# chart over `median_price`, an ops `pass_pct`. Matched as whole WORDS, not
# substrings: the old heuristic's /rent/ also fired on `n_rented`, a count.
_PERCENT_WORDS = frozenset(("pct", "percent", "share", "growth", "yoy", "rate"))
_MONEY_WORDS = frozenset(
    ("price", "prices", "rent", "sale", "sales", "value", "values", "cost", "costs", "revenue")
)
# A column called just "value" is a placeholder, not a claim about money — it is
# what every trend frame's value column is called, which is how every trend
# (bond counts included) came to be drawn in dollars.
_PLACEHOLDER_NAMES = frozenset(("value", "val", "v", "y", "metric", "measure"))
_WORDS = re.compile(r"[^a-z0-9%]+")


def unit_for_column(name: Any) -> str:
    """The unit of a metric column, or of a label derived from one.

    Resolution order: the manifest's metrics, then the derive a label carries,
    then the words in the name. A name that says nothing is ``number`` — a plain
    count is the honest reading, where guessing currency invents a dollar sign.
    """
    text = str(name or "").strip()
    if not text:
        return NUMBER
    head, _, tail = text.partition(_PIVOT_SEP)
    if tail:
        # A pivot cross-tab column: the metric is the head, except for the Δ%
        # gap column, which is a percentage whatever the metric was.
        return PERCENT if tail.strip().endswith("%") else unit_for_column(head)
    known = COLUMN_UNITS.get(text.lower())
    if known:
        return known
    suffix = _DERIVE_SUFFIX.search(text)
    if suffix:
        derive = suffix.group(1).lower()
        return DERIVE_UNITS.get(derive) or unit_for_column(text[: suffix.start()])
    if text.lower() in _PLACEHOLDER_NAMES:
        return NUMBER
    words = {w for w in _WORDS.split(text.lower()) if w}
    if "%" in text or words & _PERCENT_WORDS:
        return PERCENT
    if words & _MONEY_WORDS:
        return CURRENCY
    return NUMBER


def unit_for_measure(measure: dict[str, Any]) -> str:
    """The unit of a built measure (``_measure``'s normalised dict).

    The base aggregation keeps its source column's unit — summing or averaging
    dollars leaves dollars — except a weighted average of two same-unit legs,
    which is a ratio and so unitless. Any derive that redefines the value
    (growth / share / index / …) then wins over that base.
    """
    if measure.get("base") == "wavg":
        num_unit = unit_for_column(measure.get("num"))
        den_unit = unit_for_column(measure.get("den"))
        # $ per bond is still dollars; $ per $ is a bare ratio.
        base_unit = num_unit if den_unit == NUMBER else NUMBER
    else:
        base_unit = unit_for_column(measure.get("source"))
    return DERIVE_UNITS.get(str(measure.get("derive") or ""), base_unit)
