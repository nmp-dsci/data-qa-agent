"""Profile result → pages — the ONE assembly of the cohort comparison (s20).

The Explore Profile output is expressed as the same ``Page`` objects the chat
agent emits and the report engine renders (kpi / table / breakdown /
choropleth), so a Profile run is portable by construction: the UI renders the
pages through PageLayout, Save-as-golden persists them unchanged, and evals can
diff them. Formatting happens here (a Python port of the frontend's
``formatCell``) so every consumer shows identical values.

Deliberately dependency-light — pure stdlib, plain dicts in and out, no app.*
imports — so the data-agent's contract tests can load this module by path and
validate its output through the agent-side ``PagesEnvelope``
(``tests/test_explore_agent_sync.py``). The map object (``choropleth``) is an
Explore-only object: it appears in these pages but the agent never emits one.
"""

from __future__ import annotations

from typing import Any

# The per-predictor charts mirror the legacy tool: top-N strongest signals,
# excluding the geo dimension (the map already shows it).
MAX_PREDICTOR_CHARTS = 6
MAP_HEIGHT = 340


def _fmt_value(value: Any, fmt: str | None) -> str:
    """Port of ui/charts/DataTable.tsx formatCell — keep the two in step."""
    if value is None or value == "":
        return "—"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)
    sign = "−" if n < 0 else ""
    a = abs(n)
    if a >= 1_000_000:
        compact = f"{a / 1_000_000:.2f}M"
    elif a >= 10_000:
        compact = f"{round(a / 1000)}k"
    else:
        compact = f"{a:,.2f}".rstrip("0").rstrip(".")
    if fmt == "currency":
        return f"{sign}${compact}"
    if fmt == "percent":
        return f"{sign}{a:.2f}%"
    return f"{sign}{compact}"


def _render_filter(v: Any) -> str:
    """One filter value as display text (multi-select list, min→max range, scalar)."""
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v)
    if isinstance(v, dict):
        lo, hi = v.get("min"), v.get("max")
        arrow = " → " if (lo is not None or hi is not None) else ""
        return f"{lo if lo is not None else ''}{arrow}{hi if hi is not None else ''}".strip()
    return str(v)


def _cohort_labels(
    target_filters: dict[str, Any], comparison_filters: dict[str, Any]
) -> tuple[str, str]:
    """Name the cohorts by the filter values that DISTINGUISH them (e.g.
    "Hornsby" vs "Normanhurst"); fall back to Target / Comparison."""
    keys = list(dict.fromkeys([*target_filters, *comparison_filters]))

    def side(filters: dict[str, Any], key: str) -> str:
        return _render_filter(filters[key]) if key in filters else "any"

    distinct = [k for k in keys if side(target_filters, k) != side(comparison_filters, k)]
    target = " · ".join(side(target_filters, k) for k in distinct)
    comparison = " · ".join(side(comparison_filters, k) for k in distinct)
    if not target or not comparison or target == comparison:
        return "Target", "Comparison"
    return target, comparison


def _obj(
    obj_type: str, element_id: str, data: dict[str, Any], role: str | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {"type": obj_type, "element_id": element_id, "data": data}
    if role:
        out["role"] = role
    return out


def _kpi_objects(payload: dict[str, Any], t_label: str, c_label: str) -> list[dict[str, Any]]:
    fmt = payload.get("metric_format")
    metric_label = payload.get("metric_label", "")
    uplift: dict[str, Any] = {
        "label": "Uplift",
        "value": _fmt_value(payload.get("delta"), fmt),
    }
    delta_pct = payload.get("delta_pct")
    if isinstance(delta_pct, (int, float)):
        # growth.pct is already a percent (bypasses the fraction heuristic).
        uplift["growth"] = {"pct": delta_pct, "label": "vs comparison"}
    return [
        _obj(
            "kpi",
            "profile:kpi:target",
            {
                "label": f"{t_label} · {metric_label}",
                "value": _fmt_value(payload.get("target_total"), fmt),
                "tone": "target",
            },
            role="headline",
        ),
        _obj(
            "kpi",
            "profile:kpi:comparison",
            {
                "label": f"{c_label} · {metric_label}",
                "value": _fmt_value(payload.get("comparison_total"), fmt),
                "tone": "comparison",
            },
            role="headline",
        ),
        _obj("kpi", "profile:kpi:uplift", uplift, role="headline"),
    ]


def _map_object(payload: dict[str, Any]) -> dict[str, Any] | None:
    geo = payload.get("geo")
    if not isinstance(geo, dict):
        return None
    geo_dim = str(geo.get("dimension") or "postcode")
    predictor = next(
        (p for p in payload.get("predictors", []) if p.get("predictor") == geo_dim), None
    )
    if not predictor:
        return None
    rows = [
        {geo_dim: s.get("value"), "delta": s.get("delta")}
        for s in predictor.get("segments", [])
        if s.get("delta") is not None
    ]
    if not rows:
        return None
    return _obj(
        "choropleth",
        "profile:map",
        {
            "layer": geo.get("layer"),
            "key_field": geo_dim,
            "value_field": "delta",
            "title": f"Selection map · Δ by {geo_dim} · scroll to zoom",
            "rows": rows,
            "diverging": True,
            "height": MAP_HEIGHT,
        },
    )


def _tables_page(
    payload: dict[str, Any], t_label: str, c_label: str, dim_labels: dict[str, str]
) -> dict[str, Any]:
    metrics_table = _obj(
        "table",
        "profile:table:metrics",
        {
            "title": "Data group comparison · all metrics",
            "variant": "comparison",
            "columns": [
                {"key": "label", "label": "Metric"},
                {"key": "target", "label": t_label, "align": "right", "tone": "target"},
                {"key": "comparison", "label": c_label, "align": "right", "tone": "comparison"},
                {"key": "delta", "label": "Δ", "align": "right", "tone": "delta"},
                {
                    "key": "delta_pct",
                    "label": "Δ%",
                    "align": "right",
                    "tone": "delta",
                    "format": "percent",
                },
            ],
            "rows": [
                {
                    "label": d.get("label"),
                    "target": d.get("target"),
                    "comparison": d.get("comparison"),
                    "delta": d.get("delta"),
                    "delta_pct": d.get("delta_pct"),
                }
                for d in payload.get("metric_deltas", [])
            ],
        },
    )
    target_filters = payload.get("target_filters") or {}
    comparison_filters = payload.get("comparison_filters") or {}
    filter_keys = list(dict.fromkeys([*target_filters, *comparison_filters]))
    filters_table = _obj(
        "table",
        "profile:table:filters",
        {
            "title": "Data filters applied",
            "variant": "comparison",
            "columns": [
                {"key": "predictor", "label": "Predictor"},
                {"key": "target", "label": t_label, "tone": "target"},
                {"key": "comparison", "label": c_label, "tone": "comparison"},
            ],
            "rows": [
                {
                    "predictor": dim_labels.get(k, k),
                    "target": _render_filter(target_filters[k]) if k in target_filters else "—",
                    "comparison": _render_filter(comparison_filters[k])
                    if k in comparison_filters
                    else "—",
                }
                for k in filter_keys
            ],
        },
    )
    return {"template": "two-col", "columns": [[metrics_table], [filters_table]]}


def _uplift_table(element_id: str, title: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return _obj(
        "table",
        element_id,
        {
            "title": title,
            "variant": "ranked",
            "bar_key": "delta",
            "columns": [
                {"key": "label", "label": "Predictor"},
                {"key": "segment", "label": "Segment"},
                {"key": "delta", "label": "Δ", "align": "right", "tone": "delta"},
            ],
            "rows": rows,
        },
    )


def _predictor_chart_page(
    payload: dict[str, Any], t_label: str, c_label: str
) -> dict[str, Any] | None:
    geo = payload.get("geo")
    geo_dim = str(geo.get("dimension")) if isinstance(geo, dict) else "postcode"
    predictor_sql = payload.get("predictor_sql") or {}
    charts: list[dict[str, Any]] = []
    for p in payload.get("predictors", []):
        if p.get("predictor") == geo_dim or not p.get("segments"):
            continue
        rows: list[dict[str, Any]] = []
        for s in p["segments"]:
            rows.append({"segment": s.get("value"), "cohort": t_label, "value": s.get("target")})
            rows.append(
                {"segment": s.get("value"), "cohort": c_label, "value": s.get("comparison")}
            )
        data: dict[str, Any] = {
            "title": f"{p.get('label')} · signal {p.get('signal')}",
            "dimension": "segment",
            "measure": "value",
            "group": "cohort",
            "group_order": [t_label, c_label],
            "rows": rows,
            "height": "sm",
        }
        sql = predictor_sql.get(str(p.get("predictor")))
        if sql:
            data["sql"] = sql
        charts.append(
            _obj(
                "breakdown",
                f"profile:chart:{p.get('predictor')}",
                data,
                role="chart",
            )
        )
        if len(charts) >= MAX_PREDICTOR_CHARTS:
            break
    if not charts:
        return None
    # Two columns, filled alternately — the legacy 2-wide predictor grid.
    columns: list[list[dict[str, Any]]] = [[], []]
    for i, chart in enumerate(charts):
        columns[i % 2].append(chart)
    return {
        "template": "two-col",
        "headline": "Per-predictor comparison · strongest signal first",
        "columns": columns,
    }


def build_profile_pages(
    payload: dict[str, Any], dim_labels: dict[str, str]
) -> list[dict[str, Any]]:
    """The profile response payload (``to_public`` + dataset/filters/geo keys)
    as renderable pages. ``dim_labels`` maps dimension name → display label."""
    t_label, c_label = _cohort_labels(
        payload.get("target_filters") or {}, payload.get("comparison_filters") or {}
    )
    pages: list[dict[str, Any]] = []

    kpis = _kpi_objects(payload, t_label, c_label)
    map_obj = _map_object(payload)
    headline = f"{t_label} vs {c_label} · {payload.get('metric_label', '')}"
    if map_obj is not None:
        pages.append(
            {
                "template": "two-col",
                "headline": headline,
                "widths": [1.7, 1],
                "columns": [[map_obj], kpis],
            }
        )
    else:
        pages.append({"template": "one-col", "headline": headline, "columns": [kpis]})

    pages.append(_tables_page(payload, t_label, c_label, dim_labels))

    positives = payload.get("positive_uplifts") or []
    negatives = payload.get("negative_uplifts") or []
    if positives or negatives:
        pages.append(
            {
                "template": "two-col",
                "columns": [
                    [
                        _uplift_table(
                            "profile:table:uplifts-positive",
                            "Positive uplifts · ranked",
                            positives,
                        )
                    ],
                    [
                        _uplift_table(
                            "profile:table:uplifts-negative",
                            "Negative uplifts · ranked",
                            negatives,
                        )
                    ],
                ],
            }
        )

    chart_page = _predictor_chart_page(payload, t_label, c_label)
    if chart_page is not None:
        pages.append(chart_page)
    return pages
