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

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

# The published template registry — the frontend owns the layouts; the agent
# side may only reference these ids. Only column layouts exist (s14): a page's
# *template* is purely how many columns to render. The semantic role of a page
# (summary / insights / …) is carried separately as its ``kind`` (see PAGE_KINDS
# and the composed page dicts), so removing the old summary/insights *templates*
# never touched the agent's page plan. Kept in sync with the app.agent_config
# seed (migration 0022) and the frontend's report-engine registry.
TEMPLATE_IDS = ("one-col", "two-col", "three-col")

# Max columns per template. A page may fill fewer (empty columns collapse).
TEMPLATE_COLUMNS: dict[str, int] = {
    "one-col": 1,
    "two-col": 2,
    "three-col": 3,
}

# Semantic height names the frontend resolves (sm/md/lg → px, fill → stretch).
HEIGHT_NAMES = ("sm", "md", "lg", "fill")

# The agent-emittable object types. The frontend renders one more — "choropleth"
# — which is deliberately NOT here: the map is an Explore-tool-only object (s20
# decision), so the agent may never emit one. test_registry_sync.py asserts this
# exact relationship against the frontend sources.
ObjectType = Literal["kpi", "trend", "breakdown", "compare", "insight", "text", "table"]
TemplateId = Literal["one-col", "two-col", "three-col"]

# DataTable variants the frontend renders (see ui/charts/DataTable.tsx).
TABLE_VARIANTS = ("plain", "comparison", "ranked")


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

    @model_validator(mode="after")
    def _validate_table_data(self) -> PageObject:
        """A ``table`` object's data must be the DataTable wire shape: ``columns``
        (list of {key,label}) + ``rows`` (list of dicts), with an optional known
        ``variant`` and a ``bar_key`` (the ranked variant's inline-bar column)."""
        if self.type != "table":
            return self
        columns = self.data.get("columns")
        if not isinstance(columns, list) or not columns:
            raise ValueError("table data.columns must be a non-empty list")
        for col in columns:
            if not isinstance(col, dict) or not col.get("key") or not col.get("label"):
                raise ValueError("each table column needs a key and a label")
        if not isinstance(self.data.get("rows"), list):
            raise ValueError("table data.rows must be a list")
        variant = self.data.get("variant")
        if variant is not None and variant not in TABLE_VARIANTS:
            raise ValueError(f"table variant must be one of {'/'.join(TABLE_VARIANTS)}")
        bar_key = self.data.get("bar_key")
        if bar_key is not None and not isinstance(bar_key, str):
            raise ValueError("table bar_key must be a column key string")
        return self


class Page(BaseModel):
    """One page of the answer: a template id + ordered columns of objects."""

    template: TemplateId
    columns: list[list[PageObject]] = Field(default_factory=list)
    # Optional page-level headline summarising what the page shows (curators set
    # it in the Golden builder; the agent may compose it later). Placement/schema
    # never depend on it — it's presentation only.
    headline: str | None = None
    # Optional per-column relative widths (fr weights) overriding the template's
    # default tracks; one entry per column, left→right. None = template default.
    widths: list[float] | None = None

    @model_validator(mode="after")
    def _columns_fit_template(self) -> Page:
        limit = TEMPLATE_COLUMNS[self.template]
        if len(self.columns) > limit:
            raise ValueError(
                f"template {self.template!r} renders at most {limit} columns, "
                f"got {len(self.columns)}"
            )
        if self.widths is not None:
            if len(self.widths) > limit:
                raise ValueError(
                    f"template {self.template!r} has at most {limit} columns, "
                    f"got {len(self.widths)} widths"
                )
            if any(w <= 0 for w in self.widths):
                raise ValueError("column widths must be positive")
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


def _spec_layers(spec: dict[str, Any]) -> list[dict[str, Any]]:
    layers = spec.get("layer")
    return [ly for ly in layers if isinstance(ly, dict)] if isinstance(layers, list) else []


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


def _combo_object_from_spec(
    spec: dict[str, Any],
    *,
    element_id: str,
    role: str,
    explains: str | None,
    title: str | None,
    values: list[dict[str, Any]],
    extra: dict[str, Any],
) -> PageObject | None:
    """A layered bar+line spec (``dual_axis_chart``) → a ``compare`` object that
    carries BOTH a bar ``measure`` and a secondary-axis ``line_measure`` (plus an
    optional ``group`` series), so the frontend renders the combo instead of
    silently dropping the line layer. Returns None if it isn't a bar+line pair."""
    layers = _spec_layers(spec)
    bar = next((ly for ly in layers if _spec_mark(ly) == "bar"), None)
    line = next((ly for ly in layers if _spec_mark(ly) == "line"), None)
    if bar is None or line is None:
        return None
    bar_enc_raw = bar.get("encoding")
    line_enc_raw = line.get("encoding")
    bar_enc: dict[str, Any] = bar_enc_raw if isinstance(bar_enc_raw, dict) else {}
    line_enc: dict[str, Any] = line_enc_raw if isinstance(line_enc_raw, dict) else {}
    dim = _enc_field(bar_enc, "x")
    measure = _enc_field(bar_enc, "y")
    line_measure = _enc_field(line_enc, "y")
    if not dim or not measure or not line_measure:
        return None
    group = _enc_field(bar_enc, "xOffset") or _enc_field(bar_enc, "color")
    return PageObject(
        type="compare",
        element_id=element_id,
        role=role,
        explains=explains,
        data={
            "intent": "combo",
            "dimension": dim,
            "measure": measure,
            "line_measure": line_measure,
            "group": group,
            "title": title,
            "rows": values,
            **extra,
        },
    )


def chart_object_from_spec(
    spec: dict[str, Any] | None,
    *,
    element_id: str,
    role: str = "chart",
    height: int | str | None = "fill",
    explains: str | None = None,
    sql: str | None = None,
) -> PageObject | None:
    """Lift a validated house chart spec into a data+intent page object.

    ``sql`` — the governed query that produced the chart's rows. When present it
    rides along in ``data.sql`` so the frontend can offer "open in SQL editor"
    (parity with the Explore tab; chat/golden charts become runnable too).
    """
    if not isinstance(spec, dict):
        return None
    values = _spec_values(spec)
    if not values:
        return None
    mark = _spec_mark(spec)
    encoding = _spec_encoding(spec)
    title = _spec_title(spec)
    extra: dict[str, Any] = {} if height is None else {"height": height}
    if sql:
        extra["sql"] = sql
    # A layered bar+line spec is a dual-axis combo — lift both measures before the
    # single-mark paths (which see only the first/bar layer) can flatten it.
    if _spec_layers(spec):
        combo = _combo_object_from_spec(
            spec,
            element_id=element_id,
            role=role,
            explains=explains,
            title=title,
            values=values,
            extra=extra,
        )
        if combo is not None:
            return combo
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
# The page plan — how many pages one answer will complete for one user.
#
# Deterministic policy, never a model choice: the count must be known before
# the run starts (the frontend draws ghost slots from it) and the model can't
# promise pages it might not deliver. Pages a user is entitled to but that
# aren't buildable yet (pro's opportunities before M4) are omitted; pages
# above the user's plan stream as status:"locked" paywall teasers.
# ---------------------------------------------------------------------------

PAGE_KINDS = ("summary", "insights", "opportunities")

# What each app plan tier is entitled to see. Unknown/missing plan → free
# (the cheapest, least-revealing behaviour).
PLAN_ENTITLEMENTS: dict[str, tuple[str, ...]] = {
    "free": ("summary",),
    "plus": ("summary", "insights"),
    "pro": ("summary", "insights", "opportunities"),
}

# What the agent can actually compose today (opportunities lands with M4).
BUILDABLE_KINDS = ("summary", "insights")


def page_plan(*, plan: str) -> list[dict[str, Any]]:
    """The plan-frame payload: one slot per page kind, in order.

    ``status`` is ``building`` for page 1, ``planned`` for later pages the user
    will get, ``locked`` for pages above their plan (the paywall teaser).
    Entitled-but-unbuildable kinds are omitted entirely.
    """
    entitled = PLAN_ENTITLEMENTS.get(plan, PLAN_ENTITLEMENTS["free"])
    slots: list[dict[str, Any]] = []
    index = 0
    for kind in PAGE_KINDS:
        if kind in entitled and kind not in BUILDABLE_KINDS:
            continue
        index += 1
        if kind not in entitled:
            slots.append({"index": index, "kind": kind, "status": "locked"})
            continue
        template = kind if kind in TEMPLATE_IDS else "two-col"
        slots.append(
            {
                "index": index,
                "kind": kind,
                "template": template,
                "status": "building" if index == 1 else "planned",
            }
        )
    return slots


def planned_kinds(plan: str) -> list[str]:
    """The page kinds this user's answer will actually complete, in order."""
    return [s["kind"] for s in page_plan(plan=plan) if s["status"] != "locked"]


# ---------------------------------------------------------------------------
# Deterministic composition: InsightReport → pages (column model)
#
# Split per page so the streaming path can compose + emit each page the
# moment its inputs exist; compose_pages stays as their concatenation so the
# non-streaming path, persistence and the existing tests are unchanged.
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
    summary_page, summary_steps = compose_summary_page(report)
    insights_page, insight_steps = compose_insights_page(report)
    steps.extend(summary_steps)
    steps.extend(insight_steps)
    out = [p for p in (summary_page, insights_page) if p is not None]
    errors = [s.get("error") for s in steps if s.get("status") == "error"]
    if errors and not out:
        steps.append({"kind": "page_compose", "status": "error", "error": str(errors[0])})
        return [], steps
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


def _page(
    template: TemplateId, columns: list[list[PageObject]], *, headline: str | None = None
) -> Page:
    """Build a page, dropping empty columns (placement stays positional). An
    optional ``headline`` summarises what the page shows (presentation only)."""
    return Page(template=template, columns=[c for c in columns if c], headline=headline or None)


def _validated(page: Page) -> dict[str, Any]:
    """Re-validate through the envelope and dump the wire shape."""
    envelope = PagesEnvelope(pages=[page])
    return envelope.pages[0].model_dump(exclude_none=True)


def _as_kind(page: dict[str, Any], kind: str) -> dict[str, Any]:
    """Tag a composed page with its semantic ``kind`` (summary / insights / …).

    The *template* is only a column layout now; ``kind`` is what the streaming
    plan (page_plan / planned_kinds) keys off to place and gate each page, so it
    travels on the page dict alongside the validated template + columns.
    """
    page["kind"] = kind
    return page


def _primary_headlines(report: dict[str, Any]) -> list[dict[str, Any]]:
    headlines = [h for h in report.get("headlines", []) if isinstance(h, dict)]
    return [h for h in headlines if not h.get("related")] or headlines


def _queries_by_ref(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Report queries indexed by their ``ref`` (e.g. ``Q1``)."""
    out: dict[str, dict[str, Any]] = {}
    for q in report.get("queries", []) or []:
        if isinstance(q, dict) and q.get("ref"):
            out[str(q["ref"])] = q
    return out


def _clean_sql(sql: Any) -> str | None:
    return sql if isinstance(sql, str) and sql.strip() else None


def _primary_sql(report: dict[str, Any]) -> str | None:
    """SQL behind the summary's main chart — the governed domain query with the
    most rows (mirrors ``select_primary_query``), so the chart's open-in-SQL lands
    on the query that actually drives the answer rather than a catalog probe."""
    from .report import select_primary_query

    q = select_primary_query(_queries_by_ref(report))
    return _clean_sql(q.get("sql")) if q else None


def _sql_for_refs(report: dict[str, Any], refs: Any) -> str | None:
    """SQL of the first cited ``query_refs`` entry (insight / profile charts)."""
    if not isinstance(refs, (list, tuple)):
        return None
    by_ref = _queries_by_ref(report)
    for ref in refs:
        q = by_ref.get(str(ref))
        if q and (sql := _clean_sql(q.get("sql"))):
            return sql
    return None


def _one_line(text: str, *, limit: int = 120) -> str | None:
    """First sentence of ``text`` as a one-line headline, trimmed to ``limit``
    chars. Deterministic (no LLM): just the leading sentence of report prose."""
    prose = (text or "").strip()
    if not prose:
        return None
    match = re.match(r"\s*(.+?[.!?])(\s|$)", prose, re.DOTALL)
    head = (match.group(1) if match else prose).strip().replace("\n", " ")
    if len(head) > limit:
        head = head[: limit - 1].rstrip() + "…"
    return head or None


def _summary_headline(report: dict[str, Any]) -> str | None:
    """Headline for the summary page: the answer's key takeaway, taken from the
    report summary's first sentence. Curators can still overwrite it."""
    return _one_line(str(report.get("summary") or ""))


def _insights_headline(report: dict[str, Any]) -> str | None:
    """Headline for the insights page: what explains the numbers — the first
    insight's heading, else the first profile's. None when there are none."""
    for source in (report.get("insights"), report.get("profiles")):
        for item in source or []:
            if isinstance(item, dict) and str(item.get("heading") or "").strip():
                return _one_line(str(item["heading"]))
    return None


def _first_kpi_id(report: dict[str, Any]) -> str | None:
    """element_id of the first summary KPI — insights objects point at it."""
    primary = _primary_headlines(report)
    if not primary:
        return None
    return str(primary[0].get("element_id") or "headline:0")


def compose_summary_page(
    report: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Page 1 · Summary: the answer at a glance (build trust — values + trend).

    Column 1: headline KPI tiles + the summary note. Column 2: the main trend
    chart, height:fill so it stretches to the stacked left column. Needs only
    summary/headlines/main_chart, so it can run the moment pass 1 lands.
    Never raises; returns ``(None, error_step)`` instead.
    """
    steps: list[dict[str, Any]] = []
    try:
        summary_text = (report.get("summary") or "").strip()
        left: list[PageObject] = []
        for h in _primary_headlines(report)[:4]:
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
            report.get("main_chart"),
            element_id="report:chart",
            role="chart",
            sql=_primary_sql(report),
        )
        columns: list[list[PageObject]] = [left, [main_chart_obj] if main_chart_obj else []]
        steps.append(
            {
                "kind": "object_build",
                "status": "success",
                "page": "summary",
                "objects": [o.type for col in columns for o in col],
            }
        )
        if not any(columns):
            return None, steps
        steps.append(
            {
                "kind": "template_pick",
                "status": "success",
                "template": "two-col",
                "why": "answers lead with the latest number + trend",
            }
        )
        page = _page("two-col", columns, headline=_summary_headline(report))
        return _as_kind(_validated(page), "summary"), steps
    except (ValidationError, Exception) as exc:  # noqa: BLE001 — pages must never break an answer
        steps.append(
            {"kind": "object_build", "status": "error", "page": "summary", "error": str(exc)}
        )
        return None, steps


def compose_insights_page(
    report: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Page 2 · Insights: what explains the Page-1 numbers.

    Column 1: insight cards. Column 2: the breakdown/comparison chart that
    explains the headline (from the first insight/profile carrying a chart).
    Never raises; returns ``(None, error_step)`` instead.
    """
    steps: list[dict[str, Any]] = []
    try:
        insights = [i for i in report.get("insights", []) if isinstance(i, dict)]
        profiles = [p for p in report.get("profiles", []) if isinstance(p, dict)]
        first_kpi = _first_kpi_id(report)

        note_col: list[PageObject] = []
        chart_col: list[PageObject] = []
        # s20: a report table (skills.data_table via build_report(table=...))
        # renders as a first-class table object beside the insight cards.
        table = report.get("table")
        if isinstance(table, dict) and table.get("columns"):
            chart_col.append(
                PageObject(
                    type="table",
                    element_id="report:table",
                    role="table",
                    explains=first_kpi,
                    data={k: v for k, v in table.items() if v is not None},
                )
            )
        for source in [*insights, *profiles]:
            chart_obj = chart_object_from_spec(
                source.get("chart"),
                element_id=f"{source.get('element_id', 'insight')}:chart",
                role="chart",
                explains=first_kpi,
                # Prefer the query the insight cites; fall back to the answer's
                # primary query (the insight chart is derived from the same extract),
                # so every chat chart carries an open-in-SQL action.
                sql=_sql_for_refs(report, source.get("query_refs")) or _primary_sql(report),
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
        if not (note_col or chart_col):
            return None, steps
        steps.append(
            {
                "kind": "template_pick",
                "status": "success",
                "template": "two-col",
                "why": "insight cards / breakdown present — explain the headline",
            }
        )
        page = _page("two-col", [note_col, chart_col], headline=_insights_headline(report))
        return _as_kind(_validated(page), "insights"), steps
    except (ValidationError, Exception) as exc:  # noqa: BLE001 — pages must never break an answer
        steps.append(
            {"kind": "object_build", "status": "error", "page": "insights", "error": str(exc)}
        )
        return None, steps
