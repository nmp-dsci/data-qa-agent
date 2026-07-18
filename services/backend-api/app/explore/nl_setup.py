"""Natural-language -> Explore tool state (the Ask-AI box behind /explore/ask).

The user types "compare FY2022 vs FY2021 weekly rent for houses" and the profiler
controls populate. This module is the deterministic, offline interpreter: it maps
free text onto manifest-valid selections using keyword rules, so it needs no LLM
key and is fully unit-testable. Whatever it returns is fed through the same
`validate_spec` as a hand-built request, so it can never produce an out-of-manifest
selection. A real LLM backend can later replace `interpret_*` wholesale; its output
would pass through the identical validator.
"""

from __future__ import annotations

import re
from typing import Any

from .manifest import Dataset, get_dataset

# Words that point at a dataset (checked in priority order — yield before rent/sale).
_DATASET_HINTS: list[tuple[str, str]] = [
    ("nsw_yield", r"\byield|return on|rental return\b"),
    ("nsw_rent", r"\brent|rental|bond|lease|tenan"),
    ("nsw_sales", r"\bsale|sold|sell|price|purchase|bought\b"),
]


def infer_dataset(question: str, granted: set[str]) -> Dataset | None:
    q = question.lower()
    ordered = [slug for slug, pat in _DATASET_HINTS if re.search(pat, q)]
    for slug in ordered:
        if slug in granted and get_dataset(slug):
            return get_dataset(slug)
    # Fall back to the first granted dataset the manifest knows.
    for slug in ("nsw_sales", "nsw_rent", "nsw_yield"):
        if slug in granted and get_dataset(slug):
            return get_dataset(slug)
    return None


def _infer_metric(dataset: Dataset, q: str) -> str:
    ql = q.lower()
    names = {m.name for m in dataset.metrics}
    if "yield" in ql and "gross_yield_pct" in names:
        return "gross_yield_pct"
    wants_volume = bool(re.search(r"\b(volume|count|number of|how many|bonds|sales)\b", ql))
    wants_avg = bool(re.search(r"\b(avg|average|mean|typical)\b", ql))
    if wants_volume and not wants_avg:
        for cand in ("n_rented", "n_sold"):
            if cand in names:
                return cand
    if "total" in ql:
        for cand in ("total_weekly_rent", "total_sale_value"):
            if cand in names:
                return cand
    return dataset.default_metric


def _infer_property_type(q: str) -> str | None:
    ql = q.lower()
    if re.search(r"\bhouse", ql):
        return "house"
    if re.search(r"\bunit|apartment|flat\b", ql):
        return "unit"
    return None


def _infer_years(dataset: Dataset, q: str) -> tuple[str, list[int]] | None:
    """Return (dimension_name, [years]) — financial-year if 'FY' appears, else
    calendar. Only dimensions the dataset actually has are used."""
    q = _strip_postcode_mentions(q)
    fy = [int(y) for y in re.findall(r"\bfy\s*'?(\d{4})\b", q.lower())]
    if not fy:
        fy = [int(y) for y in re.findall(r"financial year\s+(\d{4})", q.lower())]
    if fy and dataset.dimension("year_fy"):
        return ("year_fy", fy)
    cal = [int(y) for y in re.findall(r"\b(20\d{2})\b", q)]
    if cal and dataset.dimension("year"):
        return ("year", cal)
    if cal and dataset.dimension("year_fy"):
        return ("year_fy", cal)
    return None


# "postcode 2077 [vs|versus|and [postcode] ]2076" or a lone "postcode 2077" —
# matched and stripped before year inference so a postcode digit run is never
# misread as a calendar year (see _infer_years).
_POSTCODE_PAIR = re.compile(
    r"post\s*code\s*(\d{3,4})\s*(?:vs\.?|versus|and)\s*(?:post\s*code\s*)?(\d{3,4})",
    re.IGNORECASE,
)
_POSTCODE_SINGLE = re.compile(r"post\s*code\s*(\d{3,4})", re.IGNORECASE)


def _strip_postcode_mentions(q: str) -> str:
    q = _POSTCODE_PAIR.sub(" ", q)
    return _POSTCODE_SINGLE.sub(" ", q)


def _infer_postcodes(q: str) -> list[str] | None:
    pair = _POSTCODE_PAIR.search(q)
    if pair:
        return [pair.group(1).zfill(4), pair.group(2).zfill(4)]
    single = _POSTCODE_SINGLE.findall(q)
    if single:
        return [p.zfill(4) for p in single]
    return None


# "by X" / "per X" / "stacked by X" -> a dimension name.
_SPLIT_WORDS: list[tuple[str, str]] = [
    ("bedroom_band", r"bedroom"),
    ("property_type", r"property type|house.?unit|type"),
    ("postcode", r"postcode|post code"),
    ("suburb", r"suburb|locality"),
    ("sa4_region", r"sa4"),
    ("sa3_region", r"sa3|region"),
    ("area_band", r"land size|lot size|area"),
    ("zoning", r"zoning|zone"),
]


def _infer_split(dataset: Dataset, phrase: str) -> str | None:
    for name, pat in _SPLIT_WORDS:
        if re.search(pat, phrase) and dataset.dimension(name):
            return name
    return None


def interpret_profile(
    question: str, granted: set[str], dataset: Dataset | None = None
) -> dict[str, Any]:
    """Free text -> {dataset, metric, target:{filters}, comparison:{filters}}."""
    ds = dataset or infer_dataset(question, granted)
    if ds is None:
        raise ValueError("no accessible dataset matched the question")
    metric = _infer_metric(ds, question)
    ptype = _infer_property_type(question)
    base_filters: dict[str, Any] = {}
    if ptype and ds.dimension("property_type"):
        base_filters["property_type"] = ptype

    years = _infer_years(ds, question)
    target: dict[str, Any] = dict(base_filters)
    comparison: dict[str, Any] = dict(base_filters)
    if years and len(years[1]) >= 2:
        dim, ys = years
        hi, lo = max(ys), min(ys)  # target = the newer period, comparison = older
        target[dim] = hi
        comparison[dim] = lo
    elif years and len(years[1]) == 1:
        dim, ys = years
        target[dim] = ys[0]
        comparison[dim] = ys[0] - 1

    postcodes = _infer_postcodes(question) if ds.dimension("postcode") else None
    if postcodes and len(postcodes) >= 2:
        target["postcode"] = postcodes[0]
        comparison["postcode"] = postcodes[1]
    elif postcodes and len(postcodes) == 1:
        target["postcode"] = postcodes[0]
        comparison["postcode"] = postcodes[0]

    return {
        "dataset": ds.slug,
        "metric": metric,
        "target": {"filters": target},
        "comparison": {"filters": comparison},
    }


def interpret_trends(
    question: str, granted: set[str], dataset: Dataset | None = None
) -> dict[str, Any]:
    """Free text -> {dataset, charts:[{chart_type,x,metric,split,filters}, ...]}."""
    ds = dataset or infer_dataset(question, granted)
    if ds is None:
        raise ValueError("no accessible dataset matched the question")
    ptype = _infer_property_type(question)
    base_filters: dict[str, Any] = {}
    if ptype and ds.dimension("property_type"):
        base_filters["property_type"] = ptype

    # Split on "and" so "avg rent by bedrooms as a line, and volume stacked by
    # postcode" becomes two chart clauses.
    clauses = re.split(r"\band\b|,", question.lower())
    charts: list[dict[str, Any]] = []
    for clause in clauses:
        clause = clause.strip()
        if not clause:
            continue
        split = _infer_split(ds, clause)
        if split is None and charts:
            continue  # a trailing fragment with no split of its own — skip
        chart_type = "stacked-bar" if "stack" in clause else ("bar" if "bar" in clause else "line")
        metric = _infer_metric(ds, clause if _has_metric_word(clause) else question)
        charts.append(
            {
                "chart_type": chart_type,
                "x": ds.time_dim,
                "metric": metric,
                "split": split,
                "filters": dict(base_filters),
            }
        )
        if len(charts) == 2:
            break

    if not charts:
        charts.append(
            {
                "chart_type": "line",
                "x": ds.time_dim,
                "metric": _infer_metric(ds, question),
                "split": None,
                "filters": dict(base_filters),
            }
        )
    return {"dataset": ds.slug, "charts": charts}


def _has_metric_word(clause: str) -> bool:
    return bool(
        re.search(
            r"\b(rent|price|yield|volume|count|sales|bonds|value|avg|average|total)\b", clause
        )
    )
