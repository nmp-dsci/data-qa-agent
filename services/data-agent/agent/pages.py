"""The pages contract — the agent's report-grade answer as renderable pages.

The s08 column model: an answer is an ordered list of *pages*; each page names a
frontend-owned **template** (from the published registry) and carries an ordered
list of **columns**, each an ordered list of typed **objects** with data +
intent — never chart specs or CSS. Placement is purely positional:
``columns[i][j]`` renders in column *i* (left→right), slot *j* (top→bottom).
An object's optional ``role`` keeps the semantic label ("headline", "chart",
"insight" — the old region names) for feedback/eval continuity; it never
affects placement. Objects may carry ``data.height`` (px or sm/md/lg/fill) so
a lone chart can fill an unbalanced column.

Two producers exist:

* the deterministic :func:`compose_pages` below, which derives pages from the
  InsightReport the sandbox agent already builds (summary page from headlines +
  main chart; insights page from insight cards + any breakdown chart). Chart
  data is lifted out of the validated house-style specs (``data.values`` +
  encoding fields), so no model output reaches a chart un-governed;
* the model itself, whose skills may grow page-level composition later — the
  schema here validates either producer.

``element_id``s are preserved from the report elements they derive from
(headline:0 → kpi headline:0, main chart → report:chart, insight:i → insight:i)
so element-pinned feedback and the eval loop keep working unchanged.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

# The published template registry — the frontend owns the layouts; the agent
# side may only reference these ids. Kept in sync with the app.agent_config
# seed (migration 0015) and the frontend's report-engine registry.
TEMPLATE_IDS = ("summary", "insights", "one-col", "two-col", "three-col")

# Max columns per template. A page may fill fewer (empty columns collapse).
TEMPLATE_COLUMNS: dict[str, int] = {
    "summary": 2,
    "insights": 2,
    "one-col": 1,
    "two-col": 2,
    "three-col": 3,
}

# Semantic height names the frontend resolves (sm/md/lg → px, fill → stretch).
HEIGHT_NAMES = ("sm", "md", "lg", "fill")

ObjectType = Literal["kpi", "trend", "breakdown", "compare", "insight", "text"]
TemplateId = Literal["summary", "insights", "one-col", "two-col", "three-col"]


class PageObject(BaseModel):
    """One governed object placed in a page column."""

    type: ObjectType
    element_id: str
    # Semantic label (headline / chart / insight / note …) — feedback and the
    # agent's reasoning use it; placement never does.
    role: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    # Optional wiring: which object this one explains (e.g. a breakdown that
    # decomposes a kpi's growth).
    explains: str | None = None

    @field_validator("data")
    @classmethod
    def _validate_height(cls, v: dict[str, Any]) -> dict[str, Any]:
        height = v.get("height")
        if height is None:
            return v
        if isinstance(height, bool):  # bool is an int subclass — reject explicitly
            raise ValueError("data.height must be px or one of sm/md/lg/fill")
        if isinstance(height, (int, float)):
            if not 80 <= float(height) <= 1200:
                raise ValueError("data.height px must be between 80 and 1200")
            return v
        if isinstance(height, str) and height in HEIGHT_NAMES:
            return v
        raise ValueError("data.height must be px or one of sm/md/lg/fill")


class Page(BaseModel):
    """One page of the answer: a template id + ordered columns of objects."""

    template: TemplateId
    columns: list[list[PageObject]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _columns_fit_template(self) -> "Page":
        limit = TEMPLATE_COLUMNS[self.template]
        if len(self.columns) > limit:
            raise ValueError(
                f"template {self.template!r} renders at most {limit} columns, "
                f"got {len(self.columns)}"
            )
        return self


class PagesEnvelope(BaseModel):
    """Validation envelope for a whole pages list."""

    pages: list[Page] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Chart-spec data extraction — the validated house specs are a small, known
# shape (see agent/chart.py + agent/skills/charts.py): mark line|bar (or a
# layer list), encoding.{x,y,color/xOffset}.field, data.values spliced in by
# the chart skills. That makes lifting rows + fields out deterministic.
# ---------------------------------------------------------------------------


def _spec_values(spec: dict[str, Any]) -> list[dict[str, Any]]:
    data = spec.get("data")
    if isinstance(data, dict) and isinstance(data.get("values"), list):
        return [v for v in data["values"] if isinstance(v, dict)]
    return []


def _spec_mark(spec: dict[str, Any]) -> str | None:
    mark = spec.get("mark")
    if isinstance(mark, dict):
        mark = mark.get("type")
    if isinstance(mark, str):
        return mark
    layers = spec.get("layer")
    if isinstance(layers, list) and layers:
        return _spec_mark(layers[0])
    return None


def _spec_encoding(spec: dict[str, Any]) -> dict[str, Any]:
    enc = spec.get("encoding")
    if isinstance(enc, dict):
        return enc
    layers = spec.get("layer")
    if isinstance(layers, list) and layers:
        first = layers[0]
        if isinstance(first, dict) and isinstance(first.get("encoding"), dict):
            return dict(first["encoding"])
    return {}


def _enc_field(encoding: dict[str, Any], channel: str) -> str | None:
    ch = encoding.get(channel)
    if isinstance(ch, dict):
        field = ch.get("field")
        if isinstance(field, str):
            return field
    return None


def _spec_title(spec: dict[str, Any]) -> str | None:
    title = spec.get("title")
    return title if isinstance(title, str) else None


def chart_object_from_spec(
    spec: dict[str, Any] | None,
    *,
    element_id: str,
    role: str = "chart",
    height: int | str | None = "fill",
    explains: str | None = None,
) -> PageObject | None:
    """Lift a validated house chart spec into a data+intent page object."""
    if not isinstance(spec, dict):
        return None
    values = _spec_values(spec)
    if not values:
        return None
    mark = _spec_mark(spec)
    encoding = _spec_encoding(spec)
    title = _spec_title(spec)
    extra: dict[str, Any] = {} if height is None else {"height": height}
    if mark in ("line", "area", "point"):
        x: str = _enc_field(encoding, "x") or "month"
        y: str = _enc_field(encoding, "y") or "value"
        series = _enc_field(encoding, "color")
        # trend_series rows carry series/layer even when color isn't encoded.
        if series is None and any("series" in v for v in values):
            series = "series"
        return PageObject(
            type="trend",
            element_id=element_id,
            role=role,
            explains=explains,
            data={
                "intent": "line",
                "x": x,
                "y": y,
                "series": series,
                "title": title,
                "rows": values,
                **extra,
            },
        )
    if mark == "bar":
        dim = _enc_field(encoding, "x")
        measure = _enc_field(encoding, "y")
        group = _enc_field(encoding, "xOffset") or _enc_field(encoding, "color")
        if not dim or not measure:
            return None
        obj_type: ObjectType = "compare" if group and group != dim else "breakdown"
        return PageObject(
            type=obj_type,
            element_id=element_id,
            role=role,
            explains=explains,
            data={
                "intent": "grouped-bar" if obj_type == "compare" else "bar",
                "dimension": dim,
                "measure": measure,
                "group": group,
                "title": title,
                "rows": values,
                **extra,
            },
        )
    return None


# ---------------------------------------------------------------------------
# Deterministic composition: InsightReport → pages (column model)
# ---------------------------------------------------------------------------


def compose_pages(
    report: dict[str, Any], *, question: str = ""
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Derive validated pages from an InsightReport.

    Returns ``(pages, trace_steps)``. Pages is ``[]`` (never an error) when the
    report has nothing renderable — the caller simply omits the pages key and
    the frontend falls back to the classic report renderer. Trace steps record
    object-build → template-pick → page-compose for app.query_runs.
    """
    steps: list[dict[str, Any]] = []
    try:
        pages = _compose(report, steps)
        validated = PagesEnvelope(pages=pages)
        out = [p.model_dump(exclude_none=True) for p in validated.pages]
        steps.append(
            {
                "kind": "page_compose",
                "status": "success",
                "templates": [p["template"] for p in out],
                "object_count": sum(len(col) for p in out for col in p["columns"]),
                "why": f"composed from report for: {question[:80]}" if question else "",
            }
        )
        return out, steps
    except (ValidationError, Exception) as exc:  # noqa: BLE001 — pages must never break an answer
        steps.append({"kind": "page_compose", "status": "error", "error": str(exc)})
        return [], steps


def _page(template: TemplateId, columns: list[list[PageObject]]) -> Page:
    """Build a page, dropping empty columns (placement stays positional)."""
    return Page(template=template, columns=[c for c in columns if c])


def _compose(report: dict[str, Any], steps: list[dict[str, Any]]) -> list[Page]:
    headlines = [h for h in report.get("headlines", []) if isinstance(h, dict)]
    insights = [i for i in report.get("insights", []) if isinstance(i, dict)]
    profiles = [p for p in report.get("profiles", []) if isinstance(p, dict)]
    summary_text = (report.get("summary") or "").strip()

    # --- Page 1 · Summary: the answer at a glance -------------------------
    # Column 1: headline KPI tiles + the summary note. Column 2: the main
    # trend chart, height:fill so it stretches to the stacked left column.
    left: list[PageObject] = []
    primary_headlines = [h for h in headlines if not h.get("related")] or headlines
    for h in primary_headlines[:4]:
        left.append(
            PageObject(
                type="kpi",
                element_id=str(h.get("element_id") or f"headline:{len(left)}"),
                role="headline",
                data={
                    "label": h.get("label", ""),
                    "value": h.get("value", ""),
                    "basis": h.get("basis", ""),
                },
            )
        )
    if summary_text:
        left.append(
            PageObject(
                type="text",
                element_id="report:summary",
                role="note",
                data={"text": summary_text},
            )
        )
    main_chart_obj = chart_object_from_spec(
        report.get("main_chart"), element_id="report:chart", role="chart"
    )
    summary_cols: list[list[PageObject]] = [left, [main_chart_obj] if main_chart_obj else []]
    steps.append(
        {
            "kind": "object_build",
            "status": "success",
            "page": "summary",
            "objects": [o.type for col in summary_cols for o in col],
        }
    )

    pages: list[Page] = []
    if any(summary_cols):
        steps.append(
            {
                "kind": "template_pick",
                "status": "success",
                "template": "summary",
                "why": "answers lead with the latest number + trend",
            }
        )
        pages.append(_page("summary", summary_cols))

    # --- Page 2 · Insights: what explains the top line --------------------
    # Column 1: insight cards. Column 2: the breakdown/comparison chart that
    # explains the headline (from the first insight/profile carrying a chart).
    first_kpi = next((o.element_id for o in left if o.type == "kpi"), None)

    note_col: list[PageObject] = []
    chart_col: list[PageObject] = []
    for source in [*insights, *profiles]:
        chart_obj = chart_object_from_spec(
            source.get("chart"),
            element_id=f"{source.get('element_id', 'insight')}:chart",
            role="chart",
            explains=first_kpi,
        )
        if chart_obj is not None:
            chart_col.append(chart_obj)
            break

    for ins in insights[:4]:
        note_col.append(
            PageObject(
                type="insight",
                element_id=str(ins.get("element_id") or f"insight:{len(note_col)}"),
                role="insight",
                explains=first_kpi,
                data={
                    "heading": ins.get("heading", ""),
                    "text": ins.get("body", ""),
                    "refs": list(ins.get("query_refs") or []),
                },
            )
        )
    steps.append(
        {
            "kind": "object_build",
            "status": "success",
            "page": "insights",
            "objects": [o.type for col in (note_col, chart_col) for o in col],
        }
    )
    if note_col or chart_col:
        steps.append(
            {
                "kind": "template_pick",
                "status": "success",
                "template": "insights",
                "why": "insight cards / breakdown present — explain the headline",
            }
        )
        pages.append(_page("insights", [note_col, chart_col]))

    return pages
