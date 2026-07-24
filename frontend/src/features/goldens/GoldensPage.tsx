// Golden Answer (Builder) — author eval goldens stage by stage (s14 E1).
//
// Left: per-dataset list + New. Right: the three golden stages —
//   ① SQL        (run → inspect the extract rows)
//   ② Sandbox    (run the run_analysis script → metrics/report via the agent sandbox)
//   ③ Report     (the PagesEnvelope the frontend renders — edited as JSON + preview)
// Save persists through /admin/eval-goldens; a `ready` golden is the 100/100
// benchmark the eval runner (E2) scores the agent against. A run of any upstream
// stage refreshes the extract that feeds the next — the A→B→C cascade from the plan.
import { useCallback, useEffect, useRef, useState } from "react";

import {
  ExploreDataset,
  GoldenInput,
  GoldenListItem,
  GoldenObject,
  GraderSpec,
  MeasureDerive,
  OrdinalRow,
  Page,
  PageObject,
  PageObjectType,
  PrepResult,
  SandboxMeasure,
  SandboxObjectSpec,
  SkillInfo,
  buildGoldenObject,
  createGolden,
  deleteGolden,
  draftGoldenStream,
  getAdminDatasets,
  getExploreDatasets,
  getGolden,
  getGoldenSkills,
  getOrdinals,
  listGoldens,
  prepGolden,
  putOrdinal,
  scaffoldGolden,
  updateGolden,
} from "../../lib/api";
import { ObjectBody } from "../../report-engine/PageLayout";
import { Annunciator, Annunciators } from "../../ui/flightdeck";
import { GraderEditor } from "./GraderEditor";
import { graderColumns, graderIssue, pruneGrader } from "./graderSpec";
import { ReportEditor } from "./ReportEditor";

// The dataset list comes from the registry, not a literal (s24 M1). Hardcoding
// it silently locked nsw_yield — a registered dataset since migration 0025 —
// out of golden authoring, and left every yield golden mis-tagged nsw_sales.
// This list can no longer drift from what the app actually serves.
const FALLBACK_DATASETS = ["nsw_sales", "nsw_rent"];
const TIERS = ["T1", "T2", "T3", "T4", "T5", "T6", "T7"];

interface Draft {
  id?: string;
  question: string;
  dataset: string;
  tier: string;
  as_user: string;
  tags: string[];
  holdout: boolean;
  authoring_status: string;
  golden_sql: string;
  golden_sandbox: string;
  golden_data: unknown;
  golden_report: unknown;
  golden_objects: GoldenObject[];
  grader: GraderSpec;
}

const emptyDraft = (dataset: string): Draft => ({
  question: "",
  dataset,
  tier: "T1",
  as_user: "",
  tags: [],
  holdout: false,
  authoring_status: "draft",
  golden_sql: "",
  golden_sandbox: "",
  golden_data: null,
  golden_report: null,
  golden_objects: [],
  grader: {},
});

function pagesFromReport(report: unknown): Page[] {
  if (Array.isArray(report)) return report as Page[];
  const r = report as { pages?: unknown } | null;
  return r && Array.isArray(r.pages) ? (r.pages as Page[]) : [];
}

/** Skill names referenced in the run_analysis code, in first-seen order. */
function appliedSkills(code: string): string[] {
  return Array.from(new Set(Array.from(code.matchAll(/skills\.(\w+)/g), (m) => m[1])));
}

const CHART_TYPES = new Set<string>(["trend", "breakdown", "compare"]);

/** The field mapping behind an object — what it plots / reads, as one provenance
 *  line (③ objects step): so a curator can trace enriched columns → the object. */
function objectProvenance(o: PageObject): string {
  const d = o.data as Record<string, unknown>;
  const s = (k: string) => (d[k] != null && d[k] !== "" ? String(d[k]) : null);
  if (o.type === "trend") {
    return [`x ${s("x") ?? "?"}`, `y ${s("y") ?? "?"}`, s("series") && `series ${s("series")}`]
      .filter(Boolean)
      .join(" · ");
  }
  if (o.type === "breakdown" || o.type === "compare") {
    return [
      `dim ${s("dimension") ?? "?"}`,
      `measure ${s("measure") ?? "?"}`,
      s("line_measure") && `line ${s("line_measure")}`,
      s("group") && `group ${s("group")}`,
    ]
      .filter(Boolean)
      .join(" · ");
  }
  if (o.type === "kpi") return s("basis") ? `basis ${s("basis")}` : "headline tile";
  return "";
}

/** An object's own rows as a compact table — charts carry their data inline as
 *  data.rows, so we can show the exact data behind each object. */
function objectRows(o: PageObject): { columns: string[]; rows: unknown[][] } | null {
  const raw = (o.data as { rows?: unknown }).rows;
  if (!Array.isArray(raw) || raw.length === 0) return null;
  const first = raw[0];
  if (typeof first !== "object" || first === null) return null;
  const columns = Object.keys(first as Record<string, unknown>);
  return {
    columns,
    rows: raw.map((r) => columns.map((c) => (r as Record<string, unknown>)[c])),
  };
}

// Skills that MAKE a presentation object (produce the visual), keyed by type.
// The house recipe is 1:1 — a breakdown is always a comparison_chart, a compare
// a dual_axis_chart, etc. — so intersecting with the run's skills_used attributes
// the maker skill(s) to each object reliably.
const MAKER_SKILLS: Record<string, string[]> = {
  trend: ["trend_chart"],
  breakdown: ["comparison_chart"],
  compare: ["dual_axis_chart"],
  insight: ["make_insight"],
  kpi: ["latest_value"],
  text: [],
};
// Skills that ENRICH the data — derive columns/series (rolling avg, growth…) that
// feed the objects, rather than producing a visual. Shown in the ② enrichment step.
const ENRICHMENT_SKILLS = [
  "trend_series",
  "rolling_average",
  "growth_rate",
  "top_growth",
  "gross_yield",
  "driver_analysis",
];

/** The skill(s) that built this object, from the run's skills_used (maker chips). */
function objectMakers(o: PageObject, used: string[]): string[] {
  const set = new Set(used);
  return (MAKER_SKILLS[o.type] ?? []).filter((s) => set.has(s));
}

/** The enrichment (data-transform) skills the run used — for the ② step. */
function enrichmentSkillsUsed(used: string[]): string[] {
  const set = new Set(used);
  return ENRICHMENT_SKILLS.filter((s) => set.has(s));
}

/** A small skill chip — green = made an object, blue = enriched the data. */
function skillChipStyle(kind: "maker" | "enrich"): React.CSSProperties {
  const [color, background] =
    kind === "maker"
      ? ["rgb(90,170,90)", "rgba(120,200,120,0.16)"]
      : ["rgb(120,160,255)", "rgba(120,160,255,0.14)"];
  return {
    fontFamily: "var(--mono, ui-monospace, Menlo, monospace)",
    fontSize: 10,
    borderRadius: 5,
    padding: "1px 6px",
    fontWeight: 600,
    color,
    background,
  };
}

/** A colour-coded object-type chip (kpi = blue, charts = green, text = grey). */
function typeChipStyle(type: string): React.CSSProperties {
  const [color, background] =
    type === "kpi"
      ? ["rgb(120,160,255)", "rgba(120,160,255,0.14)"]
      : CHART_TYPES.has(type)
        ? ["rgb(90,170,90)", "rgba(120,200,120,0.16)"]
        : ["rgb(150,150,158)", "rgba(128,128,128,0.14)"];
  return {
    fontFamily: "var(--mono, ui-monospace, Menlo, monospace)",
    fontSize: 10,
    textTransform: "lowercase",
    borderRadius: 5,
    padding: "1px 6px",
    fontWeight: 600,
    color,
    background,
  };
}

/** Split on a separator at paren depth 0 only (so commas inside func(a, b) or a
 *  subquery stay put) — used to lay the SELECT list out one column per line. */
function splitTopLevel(s: string, sep = ","): string[] {
  const out: string[] = [];
  let depth = 0;
  let quote: string | null = null;
  let cur = "";
  for (const ch of s) {
    if (quote) {
      cur += ch;
      if (ch === quote) quote = null;
      continue;
    }
    if (ch === "'" || ch === '"') quote = ch;
    else if (ch === "(") depth++;
    else if (ch === ")") depth--;
    if (ch === sep && depth === 0) {
      out.push(cur);
      cur = "";
    } else {
      cur += ch;
    }
  }
  if (cur.trim()) out.push(cur);
  return out;
}

/** Pretty-print SQL onto multiple lines: a newline before each major clause and
 *  one SELECT column per line. Purely cosmetic (whitespace + keyword case) — the
 *  text still runs exactly the same, so the extract it produces is unchanged. */
// Clause keywords that start a new line — compound/qualified forms first so the
// longest match wins (e.g. "LEFT JOIN" beats "JOIN", "UNION ALL" beats "UNION").
const SQL_MAJORS = [
  "LEFT OUTER JOIN",
  "RIGHT OUTER JOIN",
  "FULL OUTER JOIN",
  "LEFT JOIN",
  "RIGHT JOIN",
  "FULL JOIN",
  "INNER JOIN",
  "CROSS JOIN",
  "JOIN",
  "WITH",
  "SELECT",
  "FROM",
  "WHERE",
  "GROUP BY",
  "ORDER BY",
  "HAVING",
  "LIMIT",
  "OFFSET",
  "UNION ALL",
  "UNION",
  "ON",
];

function formatSql(raw: string): string {
  const sql = (raw ?? "").trim();
  if (!sql) return raw;
  const s = sql.replace(/\s+/g, " ");
  const upper = s.toUpperCase();
  // Break before each major clause — but ONLY at paren depth 0, so a keyword
  // inside a function or subquery (e.g. the FROM in EXTRACT(YEAR FROM month))
  // never triggers a newline.
  let out = "";
  let depth = 0;
  let quote: string | null = null;
  let i = 0;
  while (i < s.length) {
    const ch = s[i];
    if (quote) {
      out += ch;
      if (ch === quote) quote = null;
      i++;
      continue;
    }
    if (ch === "'" || ch === '"') {
      quote = ch;
      out += ch;
      i++;
      continue;
    }
    if (ch === "(") depth++;
    else if (ch === ")") depth = Math.max(0, depth - 1);
    if (ch !== "(" && ch !== ")" && depth === 0) {
      const kw = SQL_MAJORS.find((k) => {
        if (!upper.startsWith(k, i)) return false;
        const before = i === 0 ? " " : s[i - 1];
        const after = s[i + k.length] ?? " ";
        return /\s/.test(before) && /[\s(]/.test(after);
      });
      if (kw) {
        out = out.replace(/ $/, "");
        if (out) out += "\n";
        out += upper.slice(i, i + kw.length);
        i += kw.length;
        continue;
      }
    }
    out += ch;
    i++;
  }
  // One SELECT column per line (paren-aware) for the first SELECT clause —
  // whether it leads the string or follows a newline.
  out = out.replace(
    /(^|\n)SELECT (DISTINCT )?([^\n]+)/i,
    (_m, lead: string, distinct: string | undefined, cols: string) => {
      const head = distinct ? `SELECT ${distinct.trim()}` : "SELECT";
      const parts = splitTopLevel(cols).map((c) => c.trim());
      if (parts.length < 2) return `${lead}${head} ${cols.trim()}`;
      return `${lead}${head}\n  ${parts.join(",\n  ")}`;
    },
  );
  return out.replace(/\n{2,}/g, "\n").trim();
}

// The object types the Presentation Object builder can create, labelled like the
// report editor's picker so "Line + bar chart" reads the same everywhere.
const BUILDER_TYPES: { type: PageObjectType; label: string }[] = [
  { type: "compare", label: "Line + bar chart" },
  { type: "breakdown", label: "Bar chart" },
  { type: "trend", label: "Line chart" },
  { type: "kpi", label: "KPI" },
  { type: "table", label: "Table" },
];

// A measure is a BASE aggregation of its column(s) + an optional DERIVE that
// augments it over the window (s29). sum/mean are the single-column base aggs (a
// weighted average is the num/den line mode); the derive turns the base into a %
// of total, a growth rate, a rank, etc. — all deterministic, no LLM.
type MeasureAgg = "sum" | "mean";
const MEASURE_AGGS: { value: MeasureAgg; label: string }[] = [
  { value: "sum", label: "sum" },
  { value: "mean", label: "average" },
];
const MEASURE_DERIVES: { value: MeasureDerive; label: string }[] = [
  { value: "", label: "— none —" },
  { value: "share", label: "% of total" },
  { value: "growth", label: "growth %" },
  { value: "latest", label: "latest" },
  { value: "rolling", label: "rolling average" },
  { value: "index", label: "index (=100)" },
  { value: "cumulative", label: "cumulative" },
  { value: "rank", label: "rank" },
  { value: "yoy", label: "YoY %" },
];
// % of total / cumulative only mean something for an additive total, so they need
// a sum base; the time derives read a value over months, so they need month in
// the grain. The builder blocks the invalid combos the backend would reject.
const SUM_ONLY_DERIVES = new Set<MeasureDerive>(["share", "cumulative"]);
const TIME_DERIVES = new Set<MeasureDerive>([
  "growth",
  "latest",
  "rolling",
  "index",
  "cumulative",
  "yoy",
]);
// Derives only exist in the bar-family codegen (compare/breakdown/table); trend
// and kpi read the raw column, so the form hides the derive for them and the spec
// never claims one it wouldn't compute.
const HOW_OBJECT_TYPES = new Set<PageObjectType>(["compare", "breakdown", "table"]);
// A saved measure's base aggregation. Pre-s29 measures stored the base agg in
// `agg` (an augmented `how` always implied a sum base), so this reads across both.
const aggOf = (m: SandboxMeasure): MeasureAgg => (m.agg === "mean" ? "mean" : "sum");
// A saved measure's derive, mapping the pre-s29 `how` forward.
const deriveOf = (m: SandboxMeasure): MeasureDerive =>
  m.derive ??
  (m.how === "share" || m.how === "growth" || m.how === "latest" ? m.how : "");

// The builder's flat form state (compare covers every field; simpler types read a
// subset). Defaults are the house line+bar recipe so a first build just works.
interface BuilderForm {
  name: string;
  object_type: PageObjectType;
  // grain = the columns the extract is grouped to (multi-select of the dataset's
  // dimensions). The x-axis and group are chosen FROM the grain: x is multi (a
  // composite axis concat(x1,'-',x2,…)), group is a single series column.
  grain: string[];
  dimension: string[];
  group: string;
  filter: string;
  months: number;
  bar_label: string;
  bar_source: string;
  bar_agg: MeasureAgg;
  bar_derive: MeasureDerive;
  bar_months: number;
  line_label: string;
  line_mode: "wavg" | "column";
  line_num: string;
  line_den: string;
  line_source: string;
  line_agg: MeasureAgg;
  line_derive: MeasureDerive;
  line_months: number;
  instruction: string;
}

// The deterministic (advanced) builder's defaults, per dataset (s22 P2) — so a
// rent golden opens with valid rent columns instead of sales-only defaults that
// don't exist on marts.property_rent (no suburb / area_band / n_sold there).
const defaultBuilder = (dataset = "nsw_sales"): BuilderForm =>
  dataset === "nsw_rent"
    ? {
        name: "",
        object_type: "trend",
        grain: ["month", "bedroom_band"],
        dimension: ["month"],
        group: "bedroom_band",
        filter: "property_type = 'house'",
        months: 12,
        bar_label: "rentals_volume",
        bar_source: "n_rented",
        bar_agg: "sum",
        bar_derive: "",
        bar_months: 12,
        line_label: "avg_weekly_rent",
        line_mode: "wavg",
        line_num: "total_weekly_rent",
        line_den: "n_rented",
        line_source: "avg_weekly_rent",
        line_agg: "mean",
        line_derive: "",
        line_months: 6,
        instruction: "",
      }
    : {
        name: "",
        object_type: "compare",
        grain: ["month", "suburb", "area_band"],
        dimension: ["area_band"],
        group: "suburb",
        filter: "",
        months: 12,
        bar_label: "sales_volume",
        bar_source: "n_sold",
        bar_agg: "sum",
        bar_derive: "",
        bar_months: 12,
        line_label: "avg_sale_price",
        line_mode: "wavg",
        line_num: "total_sale_value",
        line_den: "n_sold",
        line_source: "avg_sale_price",
        line_agg: "mean",
        line_derive: "",
        line_months: 6,
        instruction: "",
      };

/** s22: place a freshly built object into the report draft — first column of the
 *  first (currently visible) page, creating a one-col page when the report is
 *  empty. A deep copy keeps the shared element_id (the link) while detaching the
 *  array reference; if the same element_id is already on a page (e.g. a rebuild
 *  after refining the instruction), its snapshot is replaced in place rather than
 *  left stale. */
function placeObjectInReport(pages: Page[], obj: PageObject, pi = 0, ci = 0): Page[] {
  const copy = JSON.parse(JSON.stringify(obj)) as PageObject;
  if (pages.length === 0) {
    return [{ template: "one-col", columns: [[copy]] }];
  }
  const next = JSON.parse(JSON.stringify(pages)) as Page[];
  // Replace in place if the same element_id is already on a page (a rebuild /
  // edit-in-place updates the existing card rather than duplicating it).
  let found = false;
  for (const p of next) {
    for (const col of p.columns ?? []) {
      for (let i = 0; i < col.length; i++) {
        if (col[i].element_id === copy.element_id) {
          col[i] = copy;
          found = true;
        }
      }
    }
  }
  if (found) return next;
  // A new object goes exactly where the curator chose (page pi, column ci),
  // clamped to what exists, rather than always the first column of the first page.
  const page = next[Math.min(Math.max(pi, 0), next.length - 1)];
  if (!page.columns || page.columns.length === 0) page.columns = [[]];
  const col = Math.min(Math.max(ci, 0), page.columns.length - 1);
  page.columns[col] = [...page.columns[col], copy];
  return next;
}

/** A measure from a column + base aggregation + optional derive. */
function measureFromParts(
  label: string,
  source: string,
  agg: MeasureAgg,
  derive: MeasureDerive,
  months: number,
): SandboxMeasure {
  const m: SandboxMeasure = { label, source, agg, months };
  if (derive) m.derive = derive;
  return m;
}

/** Derives only compute for the bar family (compare/breakdown/table). A derive
 *  left over after switching to trend/kpi is dropped so the spec never claims one
 *  the codegen wouldn't build. */
function effectiveDerive(objectType: PageObjectType, derive: MeasureDerive): MeasureDerive {
  return HOW_OBJECT_TYPES.has(objectType) ? derive : "";
}

function barMeasure(f: BuilderForm): SandboxMeasure {
  return measureFromParts(
    f.bar_label,
    f.bar_source,
    f.bar_agg,
    effectiveDerive(f.object_type, f.bar_derive),
    f.bar_months,
  );
}

function lineMeasure(f: BuilderForm): SandboxMeasure {
  const derive = effectiveDerive(f.object_type, f.line_derive);
  if (f.line_mode === "wavg") {
    const m: SandboxMeasure = {
      label: f.line_label,
      num: f.line_num,
      den: f.line_den,
      months: f.line_months,
    };
    if (derive) m.derive = derive;
    return m;
  }
  return measureFromParts(f.line_label, f.line_source, f.line_agg, derive, f.line_months);
}

/** Assemble the structured spec the deterministic builder emits code from. */
function specFromBuilder(f: BuilderForm): SandboxObjectSpec {
  const grain = f.grain.filter(Boolean);
  // Two or more x columns make a composite axis (concat(x1,'-',x2,…)); one stays a
  // plain string so the builder and the lift behave exactly as before.
  const dims = f.dimension.filter(Boolean);
  const spec: SandboxObjectSpec = {
    grain,
    dimension: dims.length > 1 ? dims : (dims[0] ?? ""),
    group: f.group.trim() || null,
    months: Number(f.months) || 12,
    bar_measure: barMeasure(f),
    line_measure: lineMeasure(f),
    title: f.name,
  };
  if (f.filter.trim()) spec.filter = f.filter.trim();
  if (f.instruction.trim()) spec.instruction = f.instruction.trim();
  return spec;
}

/** Invert a saved object's measure back into the builder's bar fields (base agg +
 *  derive, tolerant of the pre-s29 `how` shape). */
function barFields(m: SandboxMeasure | undefined, d: BuilderForm) {
  if (!m) {
    return {
      bar_label: d.bar_label,
      bar_source: d.bar_source,
      bar_agg: d.bar_agg,
      bar_derive: d.bar_derive,
      bar_months: d.bar_months,
    };
  }
  return {
    bar_label: m.label ?? d.bar_label,
    bar_source: m.source ?? d.bar_source,
    bar_agg: aggOf(m),
    bar_derive: deriveOf(m),
    bar_months: m.months ?? d.bar_months,
  };
}

/** Invert a saved object's line measure back into the builder's line fields — a
 *  num/den measure is a weighted average, otherwise it's a column + base agg. */
function lineFields(m: SandboxMeasure | undefined, d: BuilderForm) {
  if (!m) {
    return {
      line_mode: d.line_mode,
      line_num: d.line_num,
      line_den: d.line_den,
      line_source: d.line_source,
      line_agg: d.line_agg,
      line_derive: d.line_derive,
      line_label: d.line_label,
      line_months: d.line_months,
    };
  }
  if (m.num || m.den) {
    return {
      line_mode: "wavg" as const,
      line_num: m.num ?? d.line_num,
      line_den: m.den ?? d.line_den,
      line_source: d.line_source,
      line_agg: d.line_agg,
      line_derive: deriveOf(m),
      line_label: m.label ?? d.line_label,
      line_months: m.months ?? d.line_months,
    };
  }
  return {
    line_mode: "column" as const,
    line_num: d.line_num,
    line_den: d.line_den,
    line_source: m.source ?? d.line_source,
    line_agg: aggOf(m),
    line_derive: deriveOf(m),
    line_label: m.label ?? d.line_label,
    line_months: m.months ?? d.line_months,
  };
}

/** Seed the structured builder from an existing object's stored spec — the
 *  inverse of specFromBuilder, so "edit" re-opens the same deterministic form
 *  the object was built with. Falls back to the dataset defaults for any field
 *  the spec omits. */
function builderFromSpec(
  spec: SandboxObjectSpec,
  dataset: string,
  objectType: PageObjectType,
  name: string,
): BuilderForm {
  const d = defaultBuilder(dataset);
  const dims = Array.isArray(spec.dimension)
    ? spec.dimension
    : spec.dimension
      ? [spec.dimension]
      : [];
  return {
    ...d,
    name: spec.title || name,
    object_type: objectType,
    grain: spec.grain?.length ? spec.grain : d.grain,
    dimension: dims.length ? dims : d.dimension,
    group: spec.group ?? "",
    filter: spec.filter ?? "",
    months: spec.months ?? d.months,
    ...barFields(spec.bar_measure, d),
    ...lineFields(spec.line_measure, d),
    instruction: "",
  };
}

// The mart table each dataset extracts from — mirrors the backend MartProfile
// tables (agent/object_builder.py). Lets the builder derive its dataset from the
// SQL Extract's FROM clause rather than the golden's stored (and sometimes
// mis-tagged) `dataset`, so the grain/metric vocabulary always matches the table
// the SQL actually queries.
const TABLE_TO_DATASET: Record<string, string> = {
  "marts.property_sales": "nsw_sales",
  "marts.property_rent": "nsw_rent",
  "marts.property_yield": "nsw_yield",
};

/** The dataset a SQL extract actually queries, from its FROM clause (else null). */
function datasetFromSql(sql: string): string | null {
  const m = /\bfrom\s+(marts\.\w+)/i.exec(sql || "");
  const tbl = m?.[1]?.toLowerCase();
  return (tbl && TABLE_TO_DATASET[tbl]) || null;
}

/** The golden SQL's own WHERE predicate (whitespace-collapsed, or ""). Mirrors the
 *  backend `original_where`: the extract always keeps this filter (line 1) and the
 *  builder's `filter` field only ANDs an additional predicate on top (line 2). */
function whereFromSql(sql: string): string {
  const m =
    /\bwhere\b([\s\S]*?)(?:\bgroup\s+by\b|\border\s+by\b|\bhaving\b|\blimit\b|\bwindow\b|;|$)/i.exec(
      sql || "",
    );
  return m ? m[1].replace(/\s+/g, " ").trim() : "";
}

/** Deterministic pre-flight: does this config build? Returns the blocking reason
 *  or null. Mirrors the builder's own rules (grain-derived x/group, additive
 *  measure sources, month required for growth/latest) so the curator sees a green
 *  "will build" before pressing Run — no LLM, no round-trip. */
function buildabilityIssue(
  f: BuilderForm,
  metricOpts: { value: string; additive: boolean }[],
): string | null {
  const grain = f.grain.filter(Boolean);
  if (!grain.length) return "pick at least one grain column";
  const chart = f.object_type;
  const additive = new Set(metricOpts.filter((m) => m.additive).map((m) => m.value));
  const needsAdditive = (src: string) =>
    metricOpts.length > 0 && !additive.has(src) ? `${src} is not additive` : null;
  if (chart !== "kpi") {
    const x = f.dimension.filter(Boolean);
    if (!x.length) return "pick an x / dimension column";
    const stray = x.find((c) => !grain.includes(c));
    if (stray) return `x column ${stray} must be in the grain`;
  }
  if (f.group && !grain.includes(f.group)) return `group ${f.group} must be in the grain`;
  // A derive is only meaningful under the right base + grain: % of total /
  // cumulative need a sum base; the time derives (growth, rolling, …) need month.
  const deriveIssue = (derive: MeasureDerive, isSum: boolean, what: string): string | null => {
    if (!derive) return null;
    if (SUM_ONLY_DERIVES.has(derive) && !isSum)
      return `${what} ${derive} needs a sum aggregation`;
    if (TIME_DERIVES.has(derive) && !grain.includes("month"))
      return `${what} ${derive} needs month in the grain`;
    return null;
  };
  // The bar measure's source is summed through the window dedup, so it must be
  // additive; then its derive must fit the base/grain.
  if (HOW_OBJECT_TYPES.has(chart)) {
    const barIssue = needsAdditive(f.bar_source);
    if (barIssue) return `bars: ${barIssue} — pick an additive metric`;
    const bd = deriveIssue(f.bar_derive, f.bar_agg === "sum", "bars");
    if (bd) return bd;
  }
  // The line: wavg legs or a summed column must be additive.
  if (chart === "compare" || chart === "trend" || chart === "kpi") {
    if (f.line_mode === "wavg") {
      const n = needsAdditive(f.line_num);
      const d = needsAdditive(f.line_den);
      if (n) return `line: ${n}`;
      if (d) return `line: ${d}`;
    } else {
      const s = needsAdditive(f.line_source);
      if (s) return `line: ${s} — pick an additive metric`;
    }
    if (HOW_OBJECT_TYPES.has(chart)) {
      // wavg is not a sum, so a sum-only derive on a wavg line is rejected.
      const ld = deriveIssue(f.line_derive, f.line_mode === "column" && f.line_agg === "sum", "line");
      if (ld) return ld;
    }
  }
  return null;
}

/** A short human label for a measure — the "6-mo avg · growth" chip. */
function measureChip(m: SandboxMeasure | undefined): string | null {
  if (!m) return null;
  const win = m.months ? `${m.months}-mo ` : "";
  const base = m.num && m.den ? "wtd-avg" : m.agg === "mean" ? "avg" : "sum";
  const derive = deriveOf(m);
  return `${m.label} · ${win}${base}${derive ? ` · ${derive}` : ""}`;
}

const box: React.CSSProperties = {
  border: "1px solid var(--border)",
  background: "var(--panel)",
  borderRadius: 10,
  padding: "12px 14px",
};
const mono: React.CSSProperties = {
  fontFamily: "var(--mono, ui-monospace, Menlo, monospace)",
  fontSize: 12.5,
};
const label: React.CSSProperties = {
  fontSize: 11,
  textTransform: "uppercase",
  letterSpacing: 0.5,
  opacity: 0.7,
};

function btn(active = true): React.CSSProperties {
  return {
    border: "1px solid var(--border-2)",
    background: "var(--panel-2)",
    color: "var(--text)",
    borderRadius: 8,
    padding: "5px 12px",
    fontSize: 13,
    cursor: active ? "pointer" : "default",
    opacity: active ? 1 : 0.5,
  };
}

/** A compact scrollable data table — reused for the SQL extract, the sandbox
 *  input (df) and the augmented output, so all three read identically. */
function DataTable({
  columns,
  rows,
  max = 12,
}: {
  columns: string[];
  rows: unknown[][];
  max?: number;
}) {
  return (
    <div style={{ overflowX: "auto", marginTop: 4 }}>
      <table style={{ ...mono, borderCollapse: "collapse" }}>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c} style={{ textAlign: "left", padding: "3px 8px", opacity: 0.7 }}>
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, max).map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => (
                <td key={j} style={{ padding: "3px 8px", borderTop: "1px solid rgba(128,128,128,0.2)" }}>
                  {String(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ ...label, marginTop: 4 }}>
        {rows.length} row{rows.length === 1 ? "" : "s"}
        {rows.length > max ? ` · showing ${max}` : ""}
      </div>
    </div>
  );
}

export function GoldensPage({
  seed,
}: {
  // Deep-link from a promoted chat answer: {id, nonce}. The nonce makes the
  // effect re-fire even when the same golden is promoted twice in a row.
  seed?: { id: string; nonce: number } | null;
}) {
  const [dataset, setDataset] = useState<string>("nsw_sales");
  const [datasets, setDatasets] = useState<string[]>(FALLBACK_DATASETS);
  const [list, setList] = useState<GoldenListItem[]>([]);
  const [draft, setDraft] = useState<Draft>(() => emptyDraft("nsw_sales"));
  const [reportText, setReportText] = useState<string>("");
  const [prep, setPrep] = useState<PrepResult | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [draftStatus, setDraftStatus] = useState<string>("");
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [selectedSkills, setSelectedSkills] = useState<Set<string>>(new Set());
  const [reasoning, setReasoning] = useState<{ skill: string; why: string }[]>([]);
  // The interactive report DRAFT — edits stay here until the curator Submits,
  // which commits them to golden_report + reconciles golden_data (Goal C).
  const [pendingPages, setPendingPages] = useState<Page[]>([]);
  // When an AI object-edit rewrites the ① SQL extract, this holds the previous
  // SQL so the curator can revert it in one click (s16 Q1: auto-apply + revert).
  const [sqlRevert, setSqlRevert] = useState<string | null>(null);
  // s18: the live lifted PageObjects of the golden's NAMED presentation objects
  // (golden_objects) — populated on build + on load (via prep). Keyed by
  // element_id; these join the composed objects as sandbox link targets.
  const [builtObjects, setBuiltObjects] = useState<PageObject[]>([]);
  const [builder, setBuilder] = useState<BuilderForm>(defaultBuilder);
  const [buildMsg, setBuildMsg] = useState<string | null>(null);
  // Live chart preview of the current builder config (shown when it's green). The
  // preview is a real deterministic build minus placement — so what you see is
  // exactly what "+ Build object" will drop into the report. `previewEditId` marks
  // that the builder was seeded from an existing object (edit-in-place).
  const [previewObject, setPreviewObject] = useState<PageObject | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewEditId, setPreviewEditId] = useState<string | null>(null);
  const previewToken = useRef(0);
  const builderRef = useRef<HTMLDivElement | null>(null);
  // Where a NEW built object lands (page index, column index). Editing an object
  // ignores these — it replaces the card in place.
  const [placePage, setPlacePage] = useState(0);
  const [placeCol, setPlaceCol] = useState(0);
  // The typed vocabulary that populates the structured builder's dropdowns — each
  // dataset's dimensions (the cuts) and metrics (the columns) from the manifest,
  // so a curator can only pick columns the data actually has (no hallucination).
  const [vocab, setVocab] = useState<ExploreDataset[]>([]);
  // Set when the vocabulary fails to load — the dropdowns then only carry their
  // current value, and the builder says so instead of silently degrading.
  const [vocabError, setVocabError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    getExploreDatasets()
      .then((d) => {
        if (!live) return;
        setVocab(d);
        setVocabError(null);
      })
      .catch(() => {
        if (!live) return;
        setVocabError("column vocabulary failed to load — dropdowns only show current values");
      });
    return () => {
      live = false;
    };
  }, []);
  // s23: the dataset's ordinal band orders (data-knowledge) — the chart lift sorts
  // an ordinal x-axis (area_band, …) by these. Editable here; applied on next Run.
  const [ordinals, setOrdinals] = useState<OrdinalRow[]>([]);
  const [ordinalDraft, setOrdinalDraft] = useState<Record<string, string>>({});

  /** Preselect the skills a piece of run_analysis code already applies (plus any
   *  a run reported), so the skills panel reflects what's in the code. */
  function seedSelectedSkills(code: string, used: string[] = []) {
    setSelectedSkills(new Set([...appliedSkills(code), ...used]));
  }

  // Drive both the visual editor and the raw-JSON view from the draft pages.
  function setDraftPages(next: Page[]) {
    setPendingPages(next);
    setReportText(next.length ? JSON.stringify({ pages: next }, null, 2) : "");
  }

  // The sandbox output JSON to store as golden_data — the latest good run's
  // report (or its extract rows), else whatever is already saved.
  function reconciledData(): unknown {
    if (prep && !prep.error) {
      return prep.report ?? { columns: prep.columns, rows: prep.rows };
    }
    return draft.golden_data ?? null;
  }

  const committedPages = pagesFromReport(draft.golden_report);
  const dirtyPresentation = JSON.stringify(pendingPages) !== JSON.stringify(committedPages);

  // The objects the sandbox produced (the source of truth for the report) and the
  // objects the interactive report currently holds — linked by shared element_id.
  // Composed objects (from the run) + the NAMED built objects (golden_objects),
  // deduped by element_id so a built object wins over a composed one.
  //
  // NON-NEGOTIABLE INVARIANT: every committed report object maps to a sandbox
  // object. With no live run yet (prep null — e.g. just after loading a saved
  // golden) we derive the composed objects from the committed golden_report, so
  // the ② Sandbox always shows every object the ③ report renders, not just the
  // named ones. A real run (prep set) then supersedes this.
  const composedObjects: PageObject[] = (
    prep ? (prep.pages ?? []) : pagesFromReport(draft.golden_report)
  ).flatMap((p) => (p.columns ?? []).flat());
  const builtIds = new Set(builtObjects.map((o) => o.element_id));
  const sandboxObjects: PageObject[] = [
    ...composedObjects.filter((o) => !builtIds.has(o.element_id)),
    ...builtObjects,
  ];
  const reportObjects: PageObject[] = pendingPages.flatMap((p) => (p.columns ?? []).flat());
  const sandboxIds = new Set(sandboxObjects.map((o) => o.element_id));
  const reportIds = new Set(reportObjects.map((o) => o.element_id));
  // Coverage: how many report objects are backed by a sandbox object of the same
  // element_id — 100% means the whole presentation is generated by the sandbox.
  const coveredCount = reportObjects.filter((o) => sandboxIds.has(o.element_id)).length;

  // Grader (draft → ready): the columns the grader may name come from the ① SQL
  // extract (live run else the saved golden_data), and its expected-objects
  // options come from what the ③ report currently holds. graderBlocker is the
  // deterministic reason the golden can't yet be promoted — null means scoreable.
  const graderCols = graderColumns(draft.golden_data, prep?.columns);
  const reportObjectTypes = Array.from(
    new Set(reportObjects.map((o) => o.type).filter(Boolean) as string[]),
  );
  const graderBlocker = graderIssue(draft.grader, graderCols);

  // The authorable datasets are whatever the registry serves (s24 M1). On
  // failure the fallback list keeps the tab usable rather than empty.
  useEffect(() => {
    getAdminDatasets()
      .then((rows) => {
        const slugs = rows.map((d) => d.slug).filter(Boolean);
        if (slugs.length) setDatasets(slugs);
      })
      .catch(() => setDatasets(FALLBACK_DATASETS));
  }, []);

  const refresh = useCallback(async () => {
    try {
      setList(await listGoldens(dataset));
    } catch (e) {
      setMsg((e as Error).message);
    }
  }, [dataset]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    getGoldenSkills()
      .then((r) => setSkills(r.skills))
      .catch(() => {});
  }, []);

  // Deep-link: when a chat answer is promoted, App bumps seed.nonce — refresh
  // the list so the new draft appears, then load it into the editor.
  useEffect(() => {
    if (!seed) return;
    void refresh();
    void selectGolden(seed.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed?.nonce]);

  // s23: load the dataset's ordinal band orders whenever the golden's dataset changes.
  useEffect(() => {
    getOrdinals(draft.dataset)
      .then((rows) => {
        setOrdinals(rows);
        setOrdinalDraft(Object.fromEntries(rows.map((r) => [r.column_name, r.ordered_values.join(", ")])));
      })
      .catch(() => setOrdinals([]));
  }, [draft.dataset]);

  /** Save an edited ordinal order (comma-separated) — applied on the next Run. */
  async function saveOrdinal(column: string) {
    const values = (ordinalDraft[column] ?? "")
      .split(",")
      .map((v) => v.trim())
      .filter(Boolean);
    if (values.length === 0) return;
    setBusy(`ordinal:${column}`);
    try {
      await putOrdinal({ dataset: draft.dataset, column, ordered_values: values });
      setOrdinals((prev) =>
        prev.map((r) => (r.column_name === column ? { ...r, ordered_values: values } : r)),
      );
      setMsg(`Saved ordinal order for ${column} — press ▶ Run to see it applied.`);
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  function patch<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
  }

  function insertSkill(name: string) {
    // A valid, editable call — never a bare "skills.x(" that breaks the parse.
    const line = `out = skills.${name}(df)  # edit args`;
    patch("golden_sandbox", `${draft.golden_sandbox}${draft.golden_sandbox ? "\n" : ""}${line}`);
  }

  function toggleSkill(name: string) {
    setSelectedSkills((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  async function generateFromSkills() {
    const picked = Array.from(selectedSkills);
    if (picked.length === 0) {
      setMsg("Tick some skills first, then generate.");
      return;
    }
    setBusy("scaffold");
    setMsg(null);
    try {
      const res = await scaffoldGolden({
        question: draft.question,
        columns: prep?.columns ?? [],
        skills: picked,
      });
      if (res.code) patch("golden_sandbox", res.code);
      setReasoning(res.reasoning ?? []);
      setMsg(
        res.error
          ? `Generated with a note: ${res.error}`
          : `Regenerated run_analysis from ${picked.length} skill(s) — review & Run.`,
      );
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  function newGolden() {
    setDraft(emptyDraft(dataset));
    setReportText("");
    setPendingPages([]);
    setSelectedSkills(new Set());
    setPrep(null);
    setMsg(null);
    setSqlRevert(null);
    setBuiltObjects([]);
    setBuilder(defaultBuilder(dataset));
    setBuildMsg(null);
    setPreviewEditId(null);
    setPlacePage(0);
    setPlaceCol(0);
  }

  /** Edit an existing report object in the Structured Builder (s30): seed the
   *  builder from the object's stored spec, then scroll to it. The builder's live
   *  preview replaces the chart, and its options are the controls to change it;
   *  pressing Build re-runs the deterministic pipeline and replaces the card in
   *  place (placeObjectInReport matches on element_id). No LLM, no free text. */
  function editObjectInBuilder(o: PageObject) {
    const go = draft.golden_objects.find((g) => g.element_id === o.element_id);
    if (!go?.spec) {
      setBuildMsg(
        "This object has no builder spec to edit (it wasn't built from the structured builder). Build a new one below.",
      );
      builderRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    setBuilder(
      builderFromSpec(
        go.spec,
        datasetFromSql(draft.golden_sql) ?? draft.dataset,
        go.object_type as PageObjectType,
        go.name,
      ),
    );
    setPreviewEditId(o.element_id);
    setBuildMsg(`Editing “${go.name}” — change the options below, then Build to update it in place.`);
    builderRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  /** Replace/insert an object in a list keyed by element_id. */
  function upsertObject(list: PageObject[], obj: PageObject): PageObject[] {
    const rest = list.filter((o) => o.element_id !== obj.element_id);
    return [...rest, obj];
  }

  /** Recompute the golden's named objects against its extract (one round-trip) so
   *  builtObjects has live rows. NON-DESTRUCTIVE: every named object keeps a copy
   *  (the ``fallback`` seeded from the saved report) if its live re-run fails or is
   *  skipped, so a named object never vanishes from the ② Sandbox on load. */
  async function repopulateObjects(
    sql: string,
    objects: GoldenObject[],
    fallback: PageObject[] = [],
  ) {
    const seeded = (go: GoldenObject) => fallback.find((f) => f.element_id === go.element_id);
    if (!sql.trim() || objects.length === 0) {
      setBuiltObjects(fallback);
      return;
    }
    try {
      const res = await prepGolden({
        sql,
        objects: objects.map((o) => ({
          element_id: o.element_id,
          object_type: o.object_type,
          code: o.code,
        })),
        as_user: draft.as_user || null,
      });
      const byId = new Map(
        (res.objects_out ?? [])
          .filter((r) => r.object)
          .map((r) => [r.element_id, r.object as PageObject]),
      );
      // Fresh lift when the re-run produced one, else the seeded (saved) copy.
      const merged = objects
        .map((go) => byId.get(go.element_id) ?? seeded(go))
        .filter((o): o is PageObject => !!o);
      setBuiltObjects(merged);
    } catch {
      setBuiltObjects(fallback);
    }
  }

  async function selectGolden(id: string) {
    setBusy("load");
    setMsg(null);
    setPrep(null);
    setSqlRevert(null);
    setBuiltObjects([]);
    setBuildMsg(null);
    try {
      const g = await getGolden(id);
      const sandbox = g.golden_sandbox ?? "";
      const goldenObjects = g.golden_objects ?? [];
      const sql = formatSql(g.golden_sql ?? "");
      setDraft({
        id: g.id,
        question: g.question,
        dataset: g.dataset ?? dataset,
        tier: g.tier ?? "T1",
        as_user: g.as_user ?? "",
        tags: g.tags ?? [],
        holdout: g.holdout,
        authoring_status: g.authoring_status,
        golden_sql: sql,
        golden_sandbox: sandbox,
        golden_data: g.golden_data ?? null,
        golden_report: g.golden_report ?? null,
        golden_objects: goldenObjects,
        grader: g.grader ?? {},
      });
      // Seed the builder from the dataset the SQL actually queries (its FROM
      // table), not the golden's stored tag — a mis-tagged golden (rent SQL tagged
      // nsw_sales) must still open with rent grains/metrics.
      setBuilder(defaultBuilder(datasetFromSql(sql) ?? g.dataset ?? dataset));
      setDraftPages(pagesFromReport(g.golden_report));
      seedSelectedSkills(sandbox);
      // Seed each named object from its saved report copy so it shows immediately
      // (with data) — repopulateObjects then refreshes rows but never wipes these.
      const reportObjs = pagesFromReport(g.golden_report).flatMap((p) => (p.columns ?? []).flat());
      const seeded = goldenObjects
        .map((go) => reportObjs.find((o) => o.element_id === go.element_id))
        .filter((o): o is PageObject => !!o);
      setBuiltObjects(seeded);
      void repopulateObjects(g.golden_sql ?? "", goldenObjects, seeded);
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  /** Build (or rebuild) a named presentation object from the builder form. */
  async function buildObject() {
    const name = builder.name.trim();
    if (!name) {
      setBuildMsg("Give the object a name first.");
      return;
    }
    if (!draft.golden_sql.trim()) {
      setBuildMsg("Add the ① SQL extract first.");
      return;
    }
    const spec = specFromBuilder(builder);
    setBusy("build");
    setBuildMsg(null);
    try {
      const res = await buildGoldenObject({
        sql: draft.golden_sql,
        name,
        object_type: builder.object_type,
        spec,
        instruction: builder.instruction.trim() || undefined,
        // Build against the dataset the SQL actually queries (its FROM table), so
        // the extract rewrite uses the right mart profile even if the golden's tag
        // is wrong.
        dataset: datasetFromSql(draft.golden_sql) ?? draft.dataset,
        as_user: draft.as_user || null,
      });
      if (!res.object) {
        setBuildMsg(res.error || "The build produced no object — check the columns/measures.");
        return;
      }
      // The builder may have extended the shared extract to add the object's
      // columns — apply it and offer a one-click revert (as the AI edit does).
      if (res.sql && res.sql.trim() && res.sql.trim() !== draft.golden_sql.trim()) {
        setSqlRevert(draft.golden_sql);
        patch("golden_sql", formatSql(res.sql));
      }
      // Edit-in-place keeps the ORIGINAL object's identity: reuse previewEditId as
      // the element_id (not the name-derived one the server returns) so the edited
      // card is replaced, never duplicated — even if the name re-slugifies. A new
      // object keeps the server's element_id and lands at the chosen page/column.
      const eid = previewEditId ?? res.element_id;
      const placedObj = { ...(res.object as PageObject), element_id: eid };
      const go: GoldenObject = {
        name,
        element_id: eid,
        object_type: res.object_type,
        code: res.code,
        spec,
      };
      patch(
        "golden_objects",
        (() => {
          const rest = draft.golden_objects.filter((o) => o.element_id !== eid);
          return [...rest, go];
        })(),
      );
      setBuiltObjects((prev) => upsertObject(prev, placedObj));
      setDraftPages(placeObjectInReport(pendingPages, placedObj, placePage, placeCol));
      setPreviewEditId(null);
      setBuildMsg(
        previewEditId
          ? `Updated “${name}” in place (${res.rows.length} extract rows).${res.error ? ` · note: ${res.error}` : ""} Review & Save.`
          : `Built “${name}” (${res.rows.length} extract rows) and placed it on page ${placePage + 1}, column ${placeCol + 1}${res.error ? ` · note: ${res.error}` : ""}. Review & Save.`,
      );
    } catch (e) {
      setBuildMsg((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  /** Re-run one built object's code (after editing it) against the extract. */
  async function rerunObject(o: GoldenObject) {
    setBusy(`rerun:${o.element_id}`);
    setBuildMsg(null);
    try {
      const res = await prepGolden({
        sql: draft.golden_sql,
        objects: [{ element_id: o.element_id, object_type: o.object_type, code: o.code }],
        as_user: draft.as_user || null,
      });
      const lifted = (res.objects_out ?? []).find((r) => r.element_id === o.element_id);
      if (lifted?.object) {
        setBuiltObjects((prev) => upsertObject(prev, lifted.object as PageObject));
        setBuildMsg(`Re-ran “${o.name}”.`);
      } else {
        setBuildMsg(lifted?.error || `“${o.name}” produced no object.`);
      }
    } catch (e) {
      setBuildMsg((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  /** Edit a built object's generating code (lineage) in place. */
  function patchObjectCode(element_id: string, code: string) {
    patch(
      "golden_objects",
      draft.golden_objects.map((o) => (o.element_id === element_id ? { ...o, code } : o)),
    );
  }

  /** Remove a named object from the golden + its live lifted copy. */
  function removeObject(element_id: string) {
    patch(
      "golden_objects",
      draft.golden_objects.filter((o) => o.element_id !== element_id),
    );
    setBuiltObjects((prev) => prev.filter((o) => o.element_id !== element_id));
  }

  async function runStage(withCode: boolean) {
    if (!draft.golden_sql.trim()) {
      setMsg("Add the SQL extract first.");
      return;
    }
    setBusy(withCode ? "sandbox" : "sql");
    setMsg(null);
    try {
      const res = await prepGolden({
        sql: draft.golden_sql,
        code: withCode ? draft.golden_sandbox : "",
        as_user: draft.as_user || null,
      });
      setPrep(res);
      if (res.error) {
        setMsg(res.error);
      } else if (withCode && res.report) {
        // Sandbox metrics become the golden_data that feeds the report stage.
        patch("golden_data", res.report);
        seedSelectedSkills(draft.golden_sandbox, res.skills_used);
      } else if (!withCode) {
        patch("golden_data", { columns: res.columns, rows: res.rows });
      }
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  // Edit ONE report object from a plain-English instruction (s16 full cascade):
  // the agent rebuilds the WHOLE run_analysis (every object + the change), may
  // revise the SQL extract when the data isn't present, reruns, and returns the
  // revised sql + full recomposed pages + the lifted target. On success we apply
  // every stage together — golden_sql / golden_sandbox / golden_data / the
  // presentation — so the golden stays reproducible; on failure NOTHING is
  // clobbered. Resolves with the target's type+data (+ a refresh set when the SQL
  // changed) for ReportEditor to apply, or an error string.
  async function save() {
    if (!draft.question.trim()) {
      setMsg("A question is required.");
      return;
    }
    // Saving commits the interactive draft: the presentation on screen is what
    // persists, and golden_data is reconciled to the latest sandbox output.
    const report: unknown = pendingPages.length ? { pages: pendingPages } : null;
    const data = reconciledData();
    setBusy("save");
    setMsg(null);
    const body: GoldenInput = {
      question: draft.question,
      dataset: draft.dataset,
      tier: draft.tier,
      as_user: draft.as_user || null,
      tags: draft.tags,
      holdout: draft.holdout,
      authoring_status: draft.authoring_status,
      golden_sql: draft.golden_sql || null,
      golden_sandbox: draft.golden_sandbox || null,
      golden_data: data ?? null,
      golden_report: report,
      golden_objects: draft.golden_objects,
      grader: pruneGrader(draft.grader),
    };
    patch("golden_report", report);
    patch("golden_data", data ?? null);
    try {
      if (draft.id) {
        await updateGolden(draft.id, body);
        setMsg("Saved.");
      } else {
        const r = await createGolden(body);
        setDraft((d) => ({ ...d, id: r.id }));
        setMsg("Created.");
      }
      await refresh();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    if (!draft.id) return;
    setBusy("delete");
    try {
      await deleteGolden(draft.id);
      newGolden();
      await refresh();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function draftWithAgent() {
    if (!draft.question.trim()) {
      setMsg("Enter a question first.");
      return;
    }
    setBusy("draft");
    setMsg(null);
    setDraftStatus("starting…");
    try {
      const res = await draftGoldenStream(
        {
          question: draft.question,
          as_user: draft.as_user || null,
          dataset: draft.dataset,
        },
        (label) => setDraftStatus(label),
      );
      const hasPages = !!(res.pages && res.pages.length);
      const report = hasPages ? { pages: res.pages } : draft.golden_report;
      const sandbox = res.sandbox || draft.golden_sandbox;
      setDraft((d) => ({
        ...d,
        golden_sql: formatSql(res.sql ?? d.golden_sql),
        golden_sandbox: sandbox,
        golden_report: report,
        golden_data: { columns: res.columns, rows: res.rows },
      }));
      setDraftPages(hasPages ? (res.pages as Page[]) : pendingPages);
      seedSelectedSkills(sandbox);
      setPrep({
        columns: res.columns,
        rows: res.rows,
        row_count: res.rows.length,
        report: res.report,
        // The drafted pages ARE the sandbox output — carry them so the ② Sandbox ③
        // objects populate and the report shows 100% sandbox coverage from the off.
        pages: hasPages ? (res.pages as Page[]) : null,
        skills_used: [],
        skill_gaps: [],
        error: null,
      });
      setMsg(
        hasPages
          ? "Agent draft loaded — review & edit each stage, then save."
          : res.summary
            ? `Agent returned no chart for this question: ${res.summary.slice(0, 160)}`
            : "Agent returned no answer — try a question the dataset covers.",
      );
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  // Visual edits + the raw-JSON box both drive the DRAFT (pendingPages). Nothing
  // reaches golden_report until submitPresentation() (or Save) commits it.
  function onReportText(t: string) {
    setReportText(t);
    if (!t.trim()) {
      setPendingPages([]);
      return;
    }
    try {
      setPendingPages(pagesFromReport(JSON.parse(t)));
      setMsg(null);
    } catch {
      // keep the last valid pages on screen; Submit/Save surfaces the parse error
    }
  }

  // Submit: commit the draft presentation to golden_report AND reconcile the
  // sandbox output JSON (golden_data), so the saved golden is self-consistent.
  function submitPresentation() {
    const report = pendingPages.length ? { pages: pendingPages } : null;
    const data = reconciledData();
    patch("golden_report", report);
    patch("golden_data", data ?? null);
    setReportText(report ? JSON.stringify(report, null, 2) : "");
    setMsg(
      `Presentation submitted — golden_report (${pendingPages.length} page${
        pendingPages.length === 1 ? "" : "s"
      }) + sandbox output refreshed. Save to persist.`,
    );
  }

  // One presentation object as a collapsible card: header (type · title · id · in
  // report) + body (skills applied → provenance → its data). Built objects also
  // carry their editable generating code (lineage) + Re-run / Remove.
  function renderObjectCard(o: PageObject, built?: GoldenObject) {
    const d = o.data as Record<string, unknown>;
    const title = built?.name ?? String(d.title ?? d.label ?? d.heading ?? o.type);
    const prov = objectProvenance(o);
    const t = objectRows(o);
    const codeSkills = built ? appliedSkills(built.code) : objectMakers(o, prep?.skills_used ?? []);
    const enrichChips = codeSkills.filter((s) => ENRICHMENT_SKILLS.includes(s));
    const makerChips = codeSkills.filter((s) => !ENRICHMENT_SKILLS.includes(s));
    const metricChips = built?.spec
      ? [measureChip(built.spec.bar_measure), measureChip(built.spec.line_measure)].filter(
          (x): x is string => !!x,
        )
      : [];
    const rerunning = busy === `rerun:${o.element_id}`;
    return (
      <details
        key={o.element_id}
        data-testid={built ? `builtobj-${built.element_id}` : undefined}
        style={{
          border: `1px solid ${built ? "rgba(120,160,255,0.4)" : "rgba(120,200,120,0.35)"}`,
          borderRadius: 8,
          background: "rgba(128,128,128,0.04)",
        }}
      >
        <summary style={{ cursor: "pointer", padding: "7px 10px" }}>
          <span
            style={{ display: "inline-flex", gap: 7, alignItems: "center", flexWrap: "wrap", verticalAlign: "middle" }}
          >
            <span style={typeChipStyle(o.type)}>{o.type}</span>
            {built && <span style={{ ...label, color: "rgb(120,160,255)" }}>built</span>}
            <span style={{ fontSize: 12.5, fontWeight: 600 }}>{title}</span>
            <code
              title="unique object id — link a report object to this element_id in ③ Report"
              style={{
                fontSize: 10,
                opacity: 0.6,
                fontFamily: "var(--mono, ui-monospace, monospace)",
                border: "1px solid rgba(128,128,128,0.3)",
                borderRadius: 4,
                padding: "0 4px",
              }}
            >
              {o.element_id}
            </code>
            <span
              style={{
                ...label,
                fontSize: 10,
                color: reportIds.has(o.element_id) ? "rgb(90,170,90)" : "rgb(150,150,158)",
              }}
            >
              {reportIds.has(o.element_id) ? "✓ in report" : "○ not in report"}
            </span>
          </span>
        </summary>
        <div style={{ padding: "0 10px 9px" }}>
          {(makerChips.length > 0 || enrichChips.length > 0 || metricChips.length > 0) && (
            <div style={{ display: "flex", gap: 4, alignItems: "center", flexWrap: "wrap", marginBottom: 5 }}>
              <span style={{ ...label, opacity: 0.6 }}>skills applied:</span>
              {makerChips.map((s) => (
                <span key={s} style={skillChipStyle("maker")}>
                  {s}
                </span>
              ))}
              {enrichChips.map((s) => (
                <span key={s} style={skillChipStyle("enrich")}>
                  {s}
                </span>
              ))}
              {metricChips.map((s) => (
                <span key={s} style={skillChipStyle("enrich")}>
                  {s}
                </span>
              ))}
            </div>
          )}
          {prov && (
            <div
              style={{
                fontSize: 11,
                opacity: 0.75,
                marginBottom: 4,
                fontFamily: "var(--mono, ui-monospace, monospace)",
              }}
            >
              ← {prov}
            </div>
          )}
          {o.type === "kpi" ? (
            <div
              style={{
                display: "inline-flex",
                flexDirection: "column",
                border: "1px solid rgba(128,128,128,0.25)",
                borderRadius: 8,
                padding: "6px 12px",
                minWidth: 120,
              }}
            >
              <span style={{ fontSize: 16, fontWeight: 700, letterSpacing: -0.3 }}>
                {String(d.value ?? "—")}
              </span>
              <span style={{ ...label, opacity: 0.6 }}>{String(d.label ?? "")}</span>
            </div>
          ) : t ? (
            <DataTable columns={t.columns} rows={t.rows} max={6} />
          ) : o.type === "text" || o.type === "insight" ? (
            <div style={{ fontSize: 12, opacity: 0.85, whiteSpace: "pre-wrap" }}>
              {String(d.text ?? d.heading ?? "")}
            </div>
          ) : (
            <div style={{ ...label, opacity: 0.5 }}>no rows captured</div>
          )}
          {built && (
            <details style={{ marginTop: 8 }}>
              <summary style={{ cursor: "pointer", fontSize: 11, opacity: 0.7 }}>
                run_analysis code · the lineage — edit columns/skills then Re-run
              </summary>
              <textarea
                data-testid={`builtcode-${built.element_id}`}
                value={built.code}
                onChange={(e) => patchObjectCode(built.element_id, e.target.value)}
                spellCheck={false}
                rows={Math.min(16, Math.max(5, built.code.split("\n").length + 1))}
                style={{ ...mono, width: "100%", marginTop: 6, whiteSpace: "pre" }}
              />
            </details>
          )}
          {built && (
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8 }}>
              <button
                type="button"
                style={{ ...btn(!rerunning), padding: "3px 10px", fontSize: 12 }}
                onClick={() => void rerunObject(built)}
                disabled={rerunning}
              >
                {rerunning ? "Re-running…" : "↻ Re-run"}
              </button>
              <button
                type="button"
                style={{ ...btn(), padding: "3px 10px", fontSize: 12, color: "var(--bad)" }}
                onClick={() => removeObject(built.element_id)}
              >
                ✕ Remove
              </button>
            </div>
          )}
        </div>
      </details>
    );
  }

  // Dropdown options for the structured builder, from the current dataset's
  // typed vocabulary. The deterministic builder reads raw mart columns, so the
  // dropdowns only offer what actually builds: `dimOpts` = the mart-backed cuts
  // (geo rollups and computed year dims need a JOIN/expression the builder can't
  // emit); `additiveMetricOpts` = the additive columns (the window dedup sums —
  // a derived ratio like avg_* doesn't survive it; wtd-avg recomposes instead).
  // The builder's dataset follows the SQL Extract's FROM table (else the golden's
  // stored tag) so its vocabulary always matches the rows the SQL queries — a
  // rent SQL mis-tagged nsw_sales still offers rent dimensions/metrics.
  const builderDataset = datasetFromSql(draft.golden_sql) ?? draft.dataset;
  const dsVocab = vocab.find((d) => d.slug === builderDataset) ?? null;
  const dimOpts = (dsVocab?.dimensions ?? [])
    .filter((d) => d.source === "mart")
    .map((d) => ({ value: d.name, label: d.label }));
  const metricOpts = (dsVocab?.metrics ?? []).map((m) => ({
    value: m.name,
    label: m.label,
    additive: m.kind === "additive",
  }));
  const additiveMetricOpts = metricOpts.filter((m) => m.additive);
  // The derive dropdown only renders for the bar family — trend/kpi ignore it.
  const howApplies = HOW_OBJECT_TYPES.has(builder.object_type);
  // Every base aggregation sums the source through the window dedup, so a source
  // must be additive; snap a non-additive (e.g. legacy free-text) one to the first
  // additive metric so the invalid combo can't reach the build.
  const ensureAdditive = (source: string): string =>
    additiveMetricOpts.length > 0 && !additiveMetricOpts.some((m) => m.value === source)
      ? additiveMetricOpts[0].value
      : source;
  // A <select> whose current value is always present (even before the vocab
  // loads, or for a legacy free-text column not in the manifest), plus optional
  // extra options and a blank choice.
  const selOptions = (
    current: string,
    opts: { value: string; label: string }[],
    blank?: string,
  ) => {
    const seen = new Set(opts.map((o) => o.value));
    return (
      <>
        {blank !== undefined && <option value="">{blank}</option>}
        {current && !seen.has(current) && <option value={current}>{current}</option>}
        {opts.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </>
    );
  };
  const sel: React.CSSProperties = { fontSize: 12, padding: "2px 4px" };
  // A checkbox list for a multi-select field: every option is visible and toggled
  // with one click (no native multi-select / cmd-click). Toggling ON appends, so
  // the selection order is the click order — which the composite x-axis concat
  // (concat(x1,'-',x2,…)) follows. Any current value not in `opts` is still shown
  // checked, so a saved column the extract dropped never silently disappears.
  const checkList = (
    current: string[],
    opts: { value: string; label: string }[],
    onChange: (next: string[]) => void,
    testid: string,
  ) => {
    const seen = new Set(opts.map((o) => o.value));
    const all = [
      ...opts,
      ...current.filter((c) => c && !seen.has(c)).map((c) => ({ value: c, label: c })),
    ];
    return (
      <div
        data-testid={testid}
        role="group"
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "3px 12px",
          maxWidth: 320,
          padding: "5px 7px",
          border: "1px solid var(--border-2, rgba(128,128,128,0.35))",
          borderRadius: 6,
        }}
      >
        {all.length === 0 && <span style={{ ...label, opacity: 0.5 }}>— none —</span>}
        {all.map((o) => (
          <label
            key={o.value}
            style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12, cursor: "pointer" }}
          >
            <input
              type="checkbox"
              aria-label={o.value}
              checked={current.includes(o.value)}
              onChange={(e) =>
                onChange(
                  e.target.checked
                    ? [...current, o.value]
                    : current.filter((c) => c !== o.value),
                )
              }
            />
            {o.label}
          </label>
        ))}
      </div>
    );
  };
  // x / dimension and group are chosen FROM the grain, so their options are the
  // grain columns (labelled from the dataset vocabulary where known).
  const dimLabel = (v: string) => dimOpts.find((o) => o.value === v)?.label ?? v;
  const grainOpts = builder.grain.filter(Boolean).map((v) => ({ value: v, label: dimLabel(v) }));
  const buildBlocker = buildabilityIssue(builder, metricOpts);

  // Live preview: whenever the config is green (buildBlocker null) and an extract
  // exists, deterministically build the object — minus placement — and show its
  // chart. Debounced so typing doesn't storm the sandbox; a token guards against a
  // slow build landing after a newer edit. A blocked config clears the preview.
  const previewKey =
    buildBlocker || !draft.golden_sql.trim()
      ? ""
      : JSON.stringify({
          spec: specFromBuilder(builder),
          type: builder.object_type,
          sql: draft.golden_sql,
          user: draft.as_user,
          dataset: builderDataset,
        });
  useEffect(() => {
    if (!previewKey) {
      setPreviewObject(null);
      setPreviewError(null);
      setPreviewBusy(false);
      return;
    }
    const token = ++previewToken.current;
    setPreviewBusy(true);
    const parsed = JSON.parse(previewKey) as {
      spec: SandboxObjectSpec;
      type: PageObjectType;
      sql: string;
      user: string;
      dataset: string;
    };
    const timer = setTimeout(() => {
      void buildGoldenObject({
        sql: parsed.sql,
        name: builder.name.trim() || "preview",
        object_type: parsed.type,
        spec: parsed.spec,
        dataset: parsed.dataset,
        as_user: parsed.user || null,
      })
        .then((res) => {
          if (token !== previewToken.current) return; // a newer edit superseded this
          if (res.object) {
            setPreviewObject(res.object);
            setPreviewError(res.error || null);
          } else {
            setPreviewObject(null);
            setPreviewError(res.error || "no preview");
          }
        })
        .catch((e: unknown) => {
          if (token === previewToken.current) {
            setPreviewObject(null);
            setPreviewError((e as Error).message);
          }
        })
        .finally(() => {
          if (token === previewToken.current) setPreviewBusy(false);
        });
    }, 800);
    return () => clearTimeout(timer);
    // builder.name is intentionally excluded — renaming shouldn't re-fetch a preview.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [previewKey]);

  return (
    <section
      className="goldens-page"
      style={{
        padding: 18,
        display: "grid",
        gridTemplateColumns: "260px 1fr",
        gap: 16,
        flex: 1,
        minHeight: 0,
        overflowY: "auto",
        alignContent: "start",
      }}
    >
      {/* ---- list ---- */}
      <div style={{ ...box, alignSelf: "start" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <strong>Goldens</strong>
          <button style={btn()} onClick={newGolden}>
            + New
          </button>
        </div>
        <select
          data-testid="golden-dataset"
          value={dataset}
          onChange={(e) => setDataset(e.target.value)}
          style={{ width: "100%", margin: "10px 0", padding: 5 }}
        >
          {datasets.map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {list.length === 0 && (
            <span style={{ opacity: 0.6, fontSize: 13 }}>No goldens yet — author the first.</span>
          )}
          {list.map((g) => (
            <button
              key={g.id}
              onClick={() => void selectGolden(g.id)}
              style={{
                ...btn(),
                textAlign: "left",
                borderColor: draft.id === g.id ? "rgba(120,160,255,0.8)" : "rgba(128,128,128,0.4)",
              }}
            >
              <span style={{ fontSize: 13 }}>{g.question || "(untitled)"}</span>
              {/* s25: the status line formalises as annunciators — the tab
                  now visually rhymes with the login's TUNE waypoint. */}
              <Annunciators className="golden-lamps">
                <Annunciator state="off">{g.tier}</Annunciator>
                <Annunciator state={g.authoring_status === "ready" ? "on" : "warn"}>
                  {g.authoring_status}
                </Annunciator>
                <Annunciator state={g.has_report ? "on" : "off"}>
                  {g.has_report ? "pages" : "no pages"}
                </Annunciator>
                <Annunciator state={g.grader_kind ? "on" : "off"}>
                  {g.grader_kind ? `grader: ${g.grader_kind}` : "no grader"}
                </Annunciator>
              </Annunciators>
            </button>
          ))}
        </div>
      </div>

      {/* ---- editor ---- */}
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div style={box}>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
            <input
              data-testid="golden-question"
              value={draft.question}
              placeholder="Question, e.g. Which suburbs have the best rental yield?"
              onChange={(e) => patch("question", e.target.value)}
              style={{ flex: 1, minWidth: 260, padding: 6, fontSize: 14 }}
            />
            <select value={draft.tier} onChange={(e) => patch("tier", e.target.value)}>
              {TIERS.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <select
              data-testid="golden-status"
              value={draft.authoring_status}
              onChange={(e) => patch("authoring_status", e.target.value)}
              title={
                graderBlocker
                  ? `ready needs a valid grader — ${graderBlocker} (see the ◆ GRADER panel)`
                  : "draft = excluded from scored evals; ready = scoreable"
              }
            >
              <option value="draft">draft</option>
              {/* ready requires a dispatchable grader (same gate as the promote
                  button) so a golden is never marked scoreable without one. */}
              <option value="ready" disabled={draft.authoring_status !== "ready" && !!graderBlocker}>
                ready
              </option>
            </select>
          </div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 8 }}>
            <input
              value={draft.as_user}
              placeholder="as_user (RLS) — blank = admin"
              onChange={(e) => patch("as_user", e.target.value)}
              style={{ padding: 5, width: 220 }}
            />
            <input
              value={draft.tags.join(", ")}
              placeholder="tags, comma-separated"
              onChange={(e) =>
                patch(
                  "tags",
                  e.target.value
                    .split(",")
                    .map((t) => t.trim())
                    .filter(Boolean),
                )
              }
              style={{ padding: 5, width: 220 }}
            />
            <label style={{ fontSize: 13, display: "flex", alignItems: "center", gap: 6 }}>
              <input
                type="checkbox"
                checked={draft.holdout}
                onChange={(e) => patch("holdout", e.target.checked)}
              />
              holdout
            </label>
          </div>
          <div
            style={{ marginTop: 10, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}
          >
            <button
              type="button"
              style={{
                ...btn(busy !== "draft"),
                background: "rgba(120,160,255,0.18)",
                borderColor: "rgba(120,160,255,0.6)",
                fontWeight: 600,
              }}
              onClick={() => void draftWithAgent()}
              disabled={busy === "draft"}
            >
              {busy === "draft" ? "Drafting…" : "✨ Draft with agent (first pass)"}
            </button>
            <span style={busy === "draft" ? { ...label, opacity: 0.95 } : label}>
              {busy === "draft"
                ? `▷ ${draftStatus || "working…"}`
                : "runs the data-agent, then you review & edit each stage"}
            </span>
          </div>
        </div>

        {/* ① SQL */}
        <div style={box}>
          <div style={label}>① SQL — extraction</div>
          <textarea
            data-testid="golden-sql"
            value={draft.golden_sql}
            onChange={(e) => {
              patch("golden_sql", e.target.value);
              setSqlRevert(null); // a manual edit supersedes the AI's rewrite
            }}
            spellCheck={false}
            rows={Math.min(16, Math.max(5, draft.golden_sql.split("\n").length + 1))}
            style={{ ...mono, width: "100%", marginTop: 6, whiteSpace: "pre", lineHeight: 1.5 }}
            placeholder={"SELECT suburb, ...\nFROM mart_rent_yield\nWHERE ..."}
          />
          {sqlRevert !== null && (
            <div
              style={{
                display: "flex",
                gap: 8,
                alignItems: "center",
                flexWrap: "wrap",
                margin: "6px 0",
                padding: "5px 10px",
                borderRadius: 6,
                fontSize: 12.5,
                border: "1px solid rgba(210,140,60,0.5)",
                background: "rgba(210,140,60,0.1)",
                color: "rgb(210,140,60)",
              }}
            >
              ● The AI object-edit rewrote this SQL to add the data it needed.
              <button
                type="button"
                style={{ ...btn(), padding: "2px 10px", fontSize: 12 }}
                onClick={() => {
                  patch("golden_sql", sqlRevert);
                  setSqlRevert(null);
                }}
              >
                ↺ revert to previous SQL
              </button>
            </div>
          )}
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button style={btn(busy !== "sql")} onClick={() => void runStage(false)} disabled={busy === "sql"}>
              {busy === "sql" ? "Running…" : "▶ Run SQL"}
            </button>
            <button
              type="button"
              style={btn(!!draft.golden_sql.trim())}
              onClick={() => patch("golden_sql", formatSql(draft.golden_sql))}
              disabled={!draft.golden_sql.trim()}
              title="pretty-print onto multiple lines (does not change what runs)"
            >
              ⋯ Format
            </button>
          </div>
          {prep && !prep.error && prep.columns.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <div style={label}>extract — the exact rows the ② sandbox receives as df</div>
              <DataTable columns={prep.columns} rows={prep.rows} max={10} />
            </div>
          )}
        </div>

        {/* ② Sandbox — object-first (s18): lead with the presentation objects the
            report renders; the ① SQL extract is their input. Build/edit named
            objects here; the raw run_analysis script lives under "advanced". */}
        <details open style={box}>
          <summary style={{ ...label, cursor: "pointer" }}>
            ② Sandbox — presentation objects · the datasets your report renders (① SQL extract is the
            input · click to collapse)
          </summary>

          {/* Structured builder — the PRIMARY, deterministic (no-LLM) way to author
              an object: pick columns from the dataset's vocabulary and Run. Same
              config → same object, every time. (The NL "New object with AI" panel
              was removed in favour of this.) */}
          <div
            ref={builderRef}
            style={{
              ...box,
              marginTop: 10,
              borderColor: previewEditId ? "rgba(120,160,255,0.85)" : "rgba(120,160,255,0.55)",
              background: "rgba(120,160,255,0.06)",
            }}
          >
            <div style={{ ...label, color: "rgb(120,160,255)", marginBottom: 4 }}>
              {previewEditId
                ? "◆ Structured builder — editing this object in place; change the options, then Build"
                : "◆ Structured builder — pick columns; deterministic (no LLM)"}
            </div>
            {/* Live chart preview (s30): shown while the config is green, so what
                you see is exactly what Build will place. When editing an object the
                preview replaces its chart and the options below are the controls to
                change it. */}
            {!buildBlocker && (
              <div
                data-testid="builder-preview"
                style={{
                  marginTop: 8,
                  padding: 10,
                  border: "1px solid rgba(120,160,255,0.4)",
                  borderRadius: 8,
                  background: "var(--panel)",
                }}
              >
                <div style={{ ...label, marginBottom: 6, display: "flex", gap: 8, alignItems: "center" }}>
                  live preview
                  {previewBusy && <span style={{ opacity: 0.7 }}>· rendering…</span>}
                  {previewEditId && <span style={{ color: "rgb(120,160,255)" }}>· editing in place</span>}
                </div>
                {previewObject ? (
                  <ObjectBody o={previewObject} />
                ) : (
                  <div style={{ ...label, opacity: 0.75, color: previewError ? "var(--bad)" : undefined }}>
                    {!draft.golden_sql.trim()
                      ? "add the ① SQL extract to preview"
                      : previewBusy
                        ? "building preview…"
                        : previewError
                          ? `preview unavailable: ${previewError}`
                          : "adjust the options below to preview"}
                  </div>
                )}
              </div>
            )}
            {vocabError && (
              <div
                data-testid="builder-vocab-error"
                style={{ ...label, color: "var(--bad)", marginTop: 8 }}
              >
                ⚠ {vocabError}
              </div>
            )}
            <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 10 }}>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                <input
                  data-testid="builder-name"
                  value={builder.name}
                  placeholder="object name, e.g. line-bar-sale-volume"
                  onChange={(e) => setBuilder((b) => ({ ...b, name: e.target.value }))}
                  style={{ padding: 5, minWidth: 240, fontSize: 13 }}
                />
                <select
                  data-testid="builder-type"
                  value={builder.object_type}
                  onChange={(e) => {
                    const type = e.target.value as PageObjectType;
                    setBuilder((b) =>
                      HOW_OBJECT_TYPES.has(type)
                        ? {
                            ...b,
                            object_type: type,
                            bar_source: ensureAdditive(b.bar_source),
                            line_source:
                              b.line_mode === "column"
                                ? ensureAdditive(b.line_source)
                                : b.line_source,
                          }
                        : { ...b, object_type: type },
                    );
                  }}
                >
                  {BUILDER_TYPES.map((t) => (
                    <option key={t.type} value={t.type}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </div>
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
                <div
                  style={{ ...label, display: "flex", flexDirection: "column", gap: 3 }}
                  title="Columns the extract is grouped to. X and group are chosen from these."
                >
                  grain (tick all that apply)
                  {checkList(
                    builder.grain,
                    dimOpts,
                    (g) =>
                      setBuilder((b) => ({
                        ...b,
                        grain: g,
                        // Cascade: x and group can only be grain columns.
                        dimension: b.dimension.filter((c) => g.includes(c)),
                        group: g.includes(b.group) ? b.group : "",
                      })),
                    "builder-grain",
                  )}
                </div>
                <div
                  style={{ ...label, display: "flex", flexDirection: "column", gap: 3 }}
                  title="X-axis column(s), from the grain. Tick 2+ for a composite axis concat(x1,'-',x2,…)."
                >
                  x / dimension (from grain)
                  {checkList(
                    builder.dimension,
                    grainOpts,
                    (d) => setBuilder((b) => ({ ...b, dimension: d })),
                    "builder-dimension",
                  )}
                </div>
                <label style={label}>
                  group (from grain){" "}
                  <select
                    data-testid="builder-group"
                    value={builder.group}
                    onChange={(e) => setBuilder((b) => ({ ...b, group: e.target.value }))}
                    style={sel}
                  >
                    {selOptions(builder.group, grainOpts, "— none —")}
                  </select>
                </label>
                <label style={label}>
                  latest N months{" "}
                  <input
                    type="number"
                    value={builder.months}
                    onChange={(e) =>
                      setBuilder((b) => ({ ...b, months: Number(e.target.value) || 12 }))
                    }
                    style={{ fontSize: 12, padding: "2px 4px", width: 56 }}
                  />
                </label>
              </div>
              <div>
                <div style={{ ...label, display: "block" }}>filter (WHERE)</div>
                {/* Line 1 — the golden's own filter, carried from the ① SQL extract
                    and ALWAYS kept (an object is a summary of the same rows the
                    question scoped). Read-only; the builder never changes it. */}
                <div
                  data-testid="builder-carried-filter"
                  style={{
                    ...mono,
                    fontSize: 12,
                    padding: "3px 6px",
                    marginTop: 3,
                    borderRadius: 4,
                    border: "1px solid rgba(128,128,128,0.3)",
                    background: "rgba(128,128,128,0.08)",
                    opacity: 0.85,
                  }}
                >
                  <span style={{ opacity: 0.6 }}>1 · carried from the golden's SQL: </span>
                  {whereFromSql(draft.golden_sql) || "— none —"}
                </div>
                {/* Line 2 — an ADDITIONAL predicate, ANDed on top of line 1. */}
                <input
                  data-testid="builder-filter"
                  value={builder.filter}
                  placeholder="2 · additional filter (ANDed), e.g. property_type = 'house'"
                  onChange={(e) => setBuilder((b) => ({ ...b, filter: e.target.value }))}
                  style={{ ...mono, fontSize: 12, padding: "3px 5px", width: "100%", marginTop: 4 }}
                />
              </div>
              {/* bars = <name> = <agg> of <column> · window <N> · derive <derive> */}
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                <span style={{ ...label, color: "rgb(90,170,90)" }}>bars =</span>
                <input
                  data-testid="builder-bar-label"
                  title="metric name"
                  value={builder.bar_label}
                  onChange={(e) => setBuilder((b) => ({ ...b, bar_label: e.target.value }))}
                  style={{ fontSize: 12, padding: "2px 4px", width: 110 }}
                />
                <span style={label}>=</span>
                <select
                  data-testid="builder-bar-agg"
                  title="aggregation"
                  value={builder.bar_agg}
                  onChange={(e) =>
                    setBuilder((b) => ({
                      ...b,
                      bar_agg: e.target.value as MeasureAgg,
                      bar_source: ensureAdditive(b.bar_source),
                    }))
                  }
                  style={sel}
                >
                  {MEASURE_AGGS.map((a) => (
                    <option key={a.value} value={a.value}>
                      {a.label}
                    </option>
                  ))}
                </select>
                <span style={label}>of</span>
                <select
                  data-testid="builder-bar-source"
                  title="column"
                  value={builder.bar_source}
                  onChange={(e) => setBuilder((b) => ({ ...b, bar_source: e.target.value }))}
                  style={sel}
                >
                  {selOptions(builder.bar_source, additiveMetricOpts)}
                </select>
                <label style={label}>
                  window{" "}
                  <input
                    type="number"
                    value={builder.bar_months}
                    onChange={(e) =>
                      setBuilder((b) => ({ ...b, bar_months: Number(e.target.value) || 12 }))
                    }
                    style={{ fontSize: 12, padding: "2px 4px", width: 50 }}
                  />
                </label>
                {howApplies && (
                  <label style={label}>
                    derive{" "}
                    <select
                      data-testid="builder-bar-derive"
                      value={builder.bar_derive}
                      onChange={(e) =>
                        setBuilder((b) => ({ ...b, bar_derive: e.target.value as MeasureDerive }))
                      }
                      style={sel}
                    >
                      {MEASURE_DERIVES.map((dv) => (
                        <option key={dv.value} value={dv.value}>
                          {dv.label}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
              </div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                <span style={{ ...label, color: "rgb(120,160,255)" }}>line =</span>
                <input
                  data-testid="builder-line-label"
                  title="series label"
                  value={builder.line_label}
                  onChange={(e) => setBuilder((b) => ({ ...b, line_label: e.target.value }))}
                  style={{ fontSize: 12, padding: "2px 4px", width: 110 }}
                />
                <select
                  data-testid="builder-line-mode"
                  value={builder.line_mode}
                  onChange={(e) => {
                    const mode = e.target.value as "wavg" | "column";
                    setBuilder((b) => ({
                      ...b,
                      line_mode: mode,
                      line_source:
                        mode === "column" ? ensureAdditive(b.line_source) : b.line_source,
                    }));
                  }}
                >
                  <option value="wavg">wtd-avg</option>
                  <option value="column">column</option>
                </select>
                {builder.line_mode === "wavg" ? (
                  <>
                    <select
                      data-testid="builder-line-num"
                      title="numerator"
                      value={builder.line_num}
                      onChange={(e) => setBuilder((b) => ({ ...b, line_num: e.target.value }))}
                      style={sel}
                    >
                      {selOptions(builder.line_num, additiveMetricOpts)}
                    </select>
                    <span style={label}>/</span>
                    <select
                      data-testid="builder-line-den"
                      title="denominator"
                      value={builder.line_den}
                      onChange={(e) => setBuilder((b) => ({ ...b, line_den: e.target.value }))}
                      style={sel}
                    >
                      {selOptions(builder.line_den, additiveMetricOpts)}
                    </select>
                  </>
                ) : (
                  <>
                    <select
                      data-testid="builder-line-agg"
                      title="aggregation"
                      value={builder.line_agg}
                      onChange={(e) =>
                        setBuilder((b) => ({
                          ...b,
                          line_agg: e.target.value as MeasureAgg,
                          line_source: ensureAdditive(b.line_source),
                        }))
                      }
                      style={sel}
                    >
                      {MEASURE_AGGS.map((a) => (
                        <option key={a.value} value={a.value}>
                          {a.label}
                        </option>
                      ))}
                    </select>
                    <span style={label}>of</span>
                    <select
                      data-testid="builder-line-source"
                      title="column"
                      value={builder.line_source}
                      onChange={(e) => setBuilder((b) => ({ ...b, line_source: e.target.value }))}
                      style={sel}
                    >
                      {selOptions(builder.line_source, additiveMetricOpts)}
                    </select>
                  </>
                )}
                <label style={label}>
                  window{" "}
                  <input
                    type="number"
                    value={builder.line_months}
                    onChange={(e) =>
                      setBuilder((b) => ({ ...b, line_months: Number(e.target.value) || 6 }))
                    }
                    style={{ fontSize: 12, padding: "2px 4px", width: 50 }}
                  />
                </label>
                {howApplies && (
                  <label style={label}>
                    derive{" "}
                    <select
                      data-testid="builder-line-derive"
                      value={builder.line_derive}
                      onChange={(e) =>
                        setBuilder((b) => ({ ...b, line_derive: e.target.value as MeasureDerive }))
                      }
                      style={sel}
                    >
                      {MEASURE_DERIVES.map((dv) => (
                        <option key={dv.value} value={dv.value}>
                          {dv.label}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
              </div>
              <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                <button
                  type="button"
                  data-testid="builder-build"
                  style={{
                    ...btn(busy !== "build" && !buildBlocker),
                    background: "rgba(120,160,255,0.22)",
                    borderColor: "rgba(120,160,255,0.6)",
                    fontWeight: 600,
                  }}
                  onClick={() => void buildObject()}
                  disabled={busy === "build" || !!buildBlocker}
                  title={
                    previewEditId
                      ? "Rebuild this object from the options above and update the same card in place"
                      : "Build the object and place it at the chosen page/column"
                  }
                >
                  {busy === "build"
                    ? previewEditId
                      ? "Updating…"
                      : "Building…"
                    : previewEditId
                      ? "↻ Update object"
                      : "＋ Build object"}
                </button>
                {/* Placement: editing updates the same card in place; a new object
                    lands exactly where the curator picks (not a fixed slot). */}
                {previewEditId ? (
                  <span style={{ ...label, color: "rgb(120,160,255)" }}>
                    ↻ updates the edited object in place
                  </span>
                ) : (
                  pendingPages.length > 0 && (
                    <span
                      style={{ ...label, display: "inline-flex", gap: 6, alignItems: "center" }}
                    >
                      place at page
                      <select
                        data-testid="builder-place-page"
                        value={Math.min(placePage, pendingPages.length - 1)}
                        onChange={(e) => {
                          setPlacePage(Number(e.target.value));
                          setPlaceCol(0);
                        }}
                        style={sel}
                      >
                        {pendingPages.map((_, i) => (
                          <option key={i} value={i}>
                            {i + 1}
                          </option>
                        ))}
                      </select>
                      column
                      <select
                        data-testid="builder-place-col"
                        value={placeCol}
                        onChange={(e) => setPlaceCol(Number(e.target.value))}
                        style={sel}
                      >
                        {Array.from(
                          {
                            length:
                              pendingPages[Math.min(placePage, pendingPages.length - 1)]?.columns
                                ?.length ?? 1,
                          },
                          (_, i) => (
                            <option key={i} value={i}>
                              {i + 1}
                            </option>
                          ),
                        )}
                      </select>
                    </span>
                  )
                )}
                {/* Deterministic pre-flight: the config is validated against the same
                    rules the builder enforces, so a green tick means it WILL build —
                    no LLM, no round-trip. */}
                <span
                  data-testid="builder-check"
                  title="Deterministic — the same config always produces the same object; no LLM"
                  style={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: buildBlocker ? "var(--bad)" : "var(--good)",
                  }}
                >
                  {buildBlocker ? `✗ ${buildBlocker}` : "✓ will build · deterministic"}
                </span>
                {buildMsg && (
                  <span data-testid="builder-msg" style={{ fontSize: 12, opacity: 0.85 }}>
                    {buildMsg}
                  </span>
                )}
              </div>
            </div>
          </div>

          {/* data-knowledge — the dataset's ordinal band orders (s23). Like the skills
              catalogue, this surfaces what the agent knows about the data; editing an
              order here re-sorts that ordinal x-axis on every chart's next Run. */}
          {ordinals.length > 0 && (
            <details style={{ ...box, marginTop: 12 }}>
              <summary style={{ ...label, cursor: "pointer", color: "rgb(120,160,255)" }}>
                ✦ data-knowledge — ordinal band orders ({draft.dataset}) · applied to every chart's
                x-axis
              </summary>
              <div style={{ ...label, opacity: 0.6, marginTop: 8 }}>
                these columns are ordinal (not alphabetical) — the chart lift sorts their x-axis by
                this order · edit + Save, then ▶ Run to see it applied
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 10 }}>
                {ordinals.map((o) => {
                  const dirty =
                    (ordinalDraft[o.column_name] ?? "") !== o.ordered_values.join(", ");
                  return (
                    <div key={o.column_name} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                        <code style={{ fontSize: 12, color: "rgb(120,160,255)" }}>{o.column_name}</code>
                        <span style={{ ...label, opacity: 0.5 }}>ordered values (comma-separated)</span>
                      </div>
                      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                        <input
                          data-testid={`ordinal-${o.column_name}`}
                          value={ordinalDraft[o.column_name] ?? ""}
                          onChange={(e) =>
                            setOrdinalDraft((d) => ({ ...d, [o.column_name]: e.target.value }))
                          }
                          style={{ ...mono, fontSize: 12, padding: "4px 6px", flex: 1, minWidth: 260 }}
                        />
                        <button
                          type="button"
                          data-testid={`ordinal-save-${o.column_name}`}
                          style={btn(busy !== `ordinal:${o.column_name}` && dirty)}
                          disabled={busy === `ordinal:${o.column_name}` || !dirty}
                          onClick={() => void saveOrdinal(o.column_name)}
                        >
                          {busy === `ordinal:${o.column_name}` ? "Saving…" : "Save order"}
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </details>
          )}

          {/* Presentation Objects — built (named) + composed, each detailing its data + skills */}
          <div style={{ marginTop: 12 }}>
            <div style={label}>
              Presentation Objects · the ① SQL extract is the input · each object details the skills
              applied to build it
            </div>
            <div
              data-testid="presentation-objects"
              style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}
            >
              {draft.golden_objects.map((go) => {
                const live = builtObjects.find((o) => o.element_id === go.element_id);
                const obj: PageObject =
                  live ??
                  ({
                    type: go.object_type,
                    element_id: go.element_id,
                    role: null,
                    data: { title: go.name },
                  } as PageObject);
                return renderObjectCard(obj, go);
              })}
              {composedObjects
                .filter((o) => !builtIds.has(o.element_id))
                .map((o) => renderObjectCard(o))}
              {draft.golden_objects.length === 0 && composedObjects.length === 0 && (
                <div style={{ ...label, opacity: 0.6 }}>
                  No presentation objects yet — build one above, or “Draft with agent”.
                </div>
              )}
            </div>
          </div>

          {/* ② enrichment — the derived frames a run built (collapsed) */}
          {prep && (
            <details style={{ marginTop: 12 }}>
              <summary style={{ ...label, cursor: "pointer" }}>
                ② enrichment · derived frames the run built (avg price, growth, moving avgs…) · click
                to expand
              </summary>
              <div style={{ marginTop: 8, fontSize: 12.5 }}>
                {enrichmentSkillsUsed(prep.skills_used).length > 0 && (
                  <div
                    style={{ display: "flex", gap: 4, flexWrap: "wrap", alignItems: "center", margin: "5px 0 2px" }}
                  >
                    <span style={{ ...label, opacity: 0.7 }}>skills applied to the data:</span>
                    {enrichmentSkillsUsed(prep.skills_used).map((s) => (
                      <span key={s} style={skillChipStyle("enrich")}>
                        {s}
                      </span>
                    ))}
                  </div>
                )}
                {prep.frames && prep.frames.length > 0 ? (
                  <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 6 }}>
                    {prep.frames.map((f) => (
                      <div key={f.name} style={{ minWidth: 0 }}>
                        <div style={{ display: "flex", gap: 6, alignItems: "baseline", flexWrap: "wrap" }}>
                          <code style={{ fontSize: 12, color: "rgb(120,160,255)" }}>{f.name}</code>
                          <span style={{ ...label, opacity: 0.55 }}>
                            {f.shape[0]} × {f.shape[1]}
                          </span>
                          <span
                            style={{ ...label, opacity: 0.9, color: f.fed_object ? "rgb(90,170,90)" : undefined }}
                          >
                            {f.fed_object ? "→ chart" : "→ kpi / derived"}
                          </span>
                        </div>
                        <DataTable columns={f.columns} rows={f.rows} max={8} />
                      </div>
                    ))}
                  </div>
                ) : (
                  <div style={{ ...label, opacity: 0.6, marginTop: 4 }}>
                    no named derived frames captured — the run computed inline, or fed the extract
                    straight to a skill
                  </div>
                )}
                {prep.error && (
                  <div style={{ color: "var(--bad)", margin: "6px 0", whiteSpace: "pre-wrap" }}>
                    error: {prep.error}
                  </div>
                )}
                {prep.skill_gaps.length > 0 && (
                  <div style={{ ...label, marginTop: 4 }}>
                    skill gaps: {prep.skill_gaps.map((g) => g.need).join(", ")}
                  </div>
                )}
              </div>
            </details>
          )}

          {/* advanced — the raw run_analysis script + skills catalogue (the drafted
              single-script base report; the primary flow is the builder above). */}
          <details style={{ ...box, marginTop: 12 }}>
            <summary style={{ ...label, cursor: "pointer" }}>
              advanced — raw run_analysis script (the drafted base report) + skills catalogue
            </summary>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "minmax(0,240px) 1fr",
                gap: 10,
                marginTop: 10,
              }}
            >
              {/* available skills — click to insert; used ones are highlighted */}
              <div style={{ minWidth: 0 }}>
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    marginBottom: 4,
                    flexWrap: "wrap",
                  }}
                >
                  <span style={label}>skills · ☑ select then generate · name inserts</span>
                  <button
                    style={{ ...btn(busy !== "scaffold"), padding: "1px 8px", fontSize: 11 }}
                    onClick={() => void generateFromSkills()}
                    disabled={busy === "scaffold"}
                    title="the agent regenerates run_analysis code using exactly the selected skills"
                  >
                    {busy === "scaffold" ? "generating…" : `⟳ generate from ${selectedSkills.size}`}
                  </button>
                </div>
                <div
                  style={{
                    maxHeight: 280,
                    overflowY: "auto",
                    border: "1px solid rgba(128,128,128,0.25)",
                    borderRadius: 8,
                    padding: 6,
                  }}
                >
                  {skills.length === 0 && <span style={{ opacity: 0.6, fontSize: 12 }}>loading…</span>}
                  {skills.map((s) => {
                    const used = prep?.skills_used.includes(s.name) ?? false;
                    const picked = selectedSkills.has(s.name);
                    const why = reasoning.find((r) => r.skill === s.name)?.why;
                    return (
                      <div
                        key={s.name}
                        style={{
                          display: "flex",
                          gap: 6,
                          alignItems: "flex-start",
                          padding: "3px 6px",
                          borderRadius: 5,
                          marginBottom: 2,
                          background: used ? "rgba(120,200,120,0.18)" : "transparent",
                          borderLeft: used
                            ? "3px solid rgba(120,200,120,0.9)"
                            : "3px solid transparent",
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={picked}
                          onChange={() => toggleSkill(s.name)}
                          title="select for code generation"
                          style={{ marginTop: 2 }}
                        />
                        <div
                          style={{ minWidth: 0, flex: 1, cursor: "pointer" }}
                          title={`${s.name}${s.signature}\nclick to insert`}
                          onClick={() => insertSkill(s.name)}
                        >
                          <code style={{ fontSize: 11.5 }}>{s.name}</code>
                          <span style={{ fontSize: 10, opacity: 0.5, marginLeft: 4 }}>{s.group}</span>
                          {used && (
                            <span style={{ fontSize: 10, color: "rgb(90,170,90)", marginLeft: 4 }}>
                              ✓ used
                            </span>
                          )}
                          <div
                            style={{
                              fontSize: 10.5,
                              opacity: why ? 0.85 : 0.6,
                              color: why ? "rgb(120,160,255)" : undefined,
                              whiteSpace: "nowrap",
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                            }}
                            title={why || s.doc}
                          >
                            {why ? `↳ why: ${why}` : s.doc}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* code + run */}
              <div style={{ minWidth: 0 }}>
                {prep && !prep.error && prep.columns.length > 0 && (
                  <details
                    style={{
                      marginBottom: 8,
                      padding: 6,
                      borderRadius: 6,
                      border: "1px solid rgba(128,128,128,0.25)",
                    }}
                  >
                    <summary style={{ ...label, cursor: "pointer" }}>
                      ① SQL extract · df — the raw rows run_analysis receives ({prep.row_count} row
                      {prep.row_count === 1 ? "" : "s"}) · click to expand
                    </summary>
                    <div style={{ marginTop: 6 }}>
                      <DataTable columns={prep.columns} rows={prep.rows} max={6} />
                    </div>
                  </details>
                )}
                <div style={{ ...label, marginBottom: 4 }}>
                  run_analysis code · consumes df · the drafted base report script
                </div>
                <textarea
                  value={draft.golden_sandbox}
                  onChange={(e) => patch("golden_sandbox", e.target.value)}
                  spellCheck={false}
                  rows={6}
                  style={{ ...mono, width: "100%" }}
                  placeholder="df is your extract. e.g.  result = skills.build_report(main_chart=skills.trend_chart(skills.trend_series(df)))"
                />
                <button
                  style={btn(busy !== "sandbox")}
                  onClick={() => void runStage(true)}
                  disabled={busy === "sandbox"}
                >
                  {busy === "sandbox" ? "Running…" : "▶ Run script"}
                </button>
              </div>
            </div>
          </details>
        </details>

        {/* ③ Report */}
        <div style={box}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <div style={label}>③ Report — presentation (interactive draft)</div>
            <span style={{ ...label, opacity: 0.6 }}>
              {pendingPages.length} page(s) · {reportObjects.length} object(s)
            </span>
            {reportObjects.length > 0 && (
              <span
                data-testid="sandbox-coverage"
                title="how many report objects are backed by a ② Sandbox object of the same element_id — 100% means the whole presentation is generated by the sandbox"
                style={{
                  ...label,
                  opacity: 0.95,
                  color: coveredCount === reportObjects.length ? "rgb(90,170,90)" : "rgb(210,140,60)",
                }}
              >
                {coveredCount === reportObjects.length ? "✓ " : "● "}
                {coveredCount}/{reportObjects.length} sandbox-backed
                {" · "}
                {Math.round((coveredCount / reportObjects.length) * 100)}%
              </span>
            )}
            {dirtyPresentation && (
              <span style={{ ...label, color: "rgb(210,140,60)", opacity: 0.95 }}>
                ● unsubmitted edits
              </span>
            )}
            <button
              type="button"
              style={{
                ...btn(),
                marginLeft: "auto",
                fontWeight: 600,
                background: dirtyPresentation ? "rgba(120,160,255,0.22)" : "rgba(128,128,128,0.08)",
                borderColor: dirtyPresentation ? "rgba(120,160,255,0.6)" : "rgba(128,128,128,0.4)",
              }}
              onClick={submitPresentation}
              title="commit these edits to golden_report + refresh the sandbox output JSON (golden_data)"
            >
              ⤴ Submit presentation
            </button>
          </div>
          <div style={{ ...label, opacity: 0.5, marginTop: 3 }}>
            move objects across columns · add / edit / remove objects & pages · then Submit to refresh
            golden_report + sandbox output JSON (the golden answer)
          </div>
          <div style={{ marginTop: 8 }}>
            <ReportEditor
              pages={pendingPages}
              onChange={setDraftPages}
              onEditInBuilder={editObjectInBuilder}
              sandboxObjects={sandboxObjects}
            />
          </div>
          <details style={{ marginTop: 10 }}>
            <summary style={{ cursor: "pointer", fontSize: 12, opacity: 0.7 }}>
              raw golden_report JSON (draft — edit here or above; Submit / Save commits it)
            </summary>
            <textarea
              value={reportText}
              onChange={(e) => onReportText(e.target.value)}
              spellCheck={false}
              rows={8}
              style={{ ...mono, width: "100%", marginTop: 6 }}
              placeholder='{ "pages": [ { "template": "two-col", "columns": [[], []] } ] }'
            />
          </details>
          <details style={{ marginTop: 6 }}>
            <summary style={{ cursor: "pointer", fontSize: 12, opacity: 0.7 }}>
              sandbox output JSON (golden_data — reconciled on Submit / Save)
            </summary>
            <pre
              style={{
                ...mono,
                overflowX: "auto",
                maxHeight: 220,
                background: "rgba(128,128,128,0.08)",
                padding: 8,
                borderRadius: 6,
                marginTop: 6,
              }}
            >
              {draft.golden_data
                ? JSON.stringify(draft.golden_data, null, 2)
                : "— run ② Sandbox, then Submit —"}
            </pre>
          </details>
        </div>

        <GraderEditor
          grader={draft.grader}
          onChange={(gr) => patch("grader", gr)}
          columns={graderCols}
          reportObjectTypes={reportObjectTypes}
          tier={draft.tier}
          status={draft.authoring_status}
          onStatusChange={(s) => patch("authoring_status", s)}
        />

        {/* actions */}
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <button
            data-testid="golden-save"
            style={btn(busy !== "save")}
            onClick={() => void save()}
            disabled={busy === "save"}
          >
            {draft.id ? "Save golden" : "Create golden"}
          </button>
          {draft.id && (
            <button style={{ ...btn(), color: "var(--bad)" }} onClick={() => void remove()}>
              Delete
            </button>
          )}
          {msg && <span style={{ fontSize: 13, opacity: 0.85 }}>{msg}</span>}
        </div>
      </div>
    </section>
  );
}
