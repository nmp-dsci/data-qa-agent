// Grader-spec logic for the Golden Examples tab — pure, no React, no I/O, so the
// deterministic "ready to promote" check the editor shows is exactly what the
// eval pipeline enforces. Every rule here mirrors the CI pack-lint
// (tests/test_eval_pack.py :: test_grader_spec_is_dispatchable +
// test_grader_columns_exist_in_golden_sql) and the runner dispatch
// (services/data-agent/agent/eval_graders.py). A golden is only "ready" — i.e.
// scoreable — when graderIssue() returns null.
import type { GraderSpec } from "../../lib/api";

export const GRADER_KINDS = ["scalar", "row_set", "ranked_set", "series"] as const;
export type GraderKind = (typeof GRADER_KINDS)[number];

// Human labels + one-line semantics for each kind (shown in the editor).
export const GRADER_KIND_INFO: Record<GraderKind, { label: string; hint: string }> = {
  scalar: { label: "scalar — one value", hint: "compare a single number within a % tolerance" },
  row_set: { label: "row_set — a set", hint: "F1 over the set of key values (order-insensitive)" },
  ranked_set: { label: "ranked_set — a top-k", hint: "overlap of the golden's top-k keys" },
  series: { label: "series — a curve", hint: "per-point tolerance on key → value" },
};

// The page object types a report can contain (G3-structural / expected_objects).
export const REPORT_OBJECT_TYPES = ["trend", "breakdown", "compare", "kpi", "table"] as const;

// AGENTS.md tier ladder → the grader kind that tier usually implies. Only a
// starting suggestion; the curator can pick any kind.
export const TIER_DEFAULT_KIND: Partial<Record<string, GraderKind>> = {
  T1: "scalar",
  T2: "series",
  T6: "ranked_set",
  T7: "ranked_set",
};

/** The columns a grader may reference — the columns golden_sql produces. Prefers
 *  a live extract run (prep.columns), else the saved golden_data, which is stored
 *  either as `{columns, rows}` or as an array of row objects. */
export function graderColumns(goldenData: unknown, liveColumns?: string[] | null): string[] {
  if (liveColumns && liveColumns.length) return liveColumns;
  const d = goldenData as { columns?: unknown; rows?: unknown } | unknown[] | null;
  if (!d) return [];
  if (Array.isArray(d)) return rowKeys(d);
  if (Array.isArray(d.columns)) return d.columns.map(String);
  if (Array.isArray(d.rows)) return rowKeys(d.rows);
  return [];
}

function rowKeys(rows: unknown[]): string[] {
  const first = rows[0];
  return first && typeof first === "object" ? Object.keys(first as object) : [];
}

/** The key column(s) a grader keys on. A single column is stored as `key`; a
 *  composite is `key: "_key"` + `key_fields` (the runner joins them). */
export function keyColumns(g: GraderSpec): string[] {
  if (g.key === "_key") return (g.key_fields ?? []).filter(Boolean);
  return g.key ? [g.key] : [];
}

/** Encode a chosen set of key columns back onto a spec: one → `key`, many →
 *  `key: "_key"` + `key_fields` (mirrors the composite-x builder idiom). */
export function withKeyColumns(g: GraderSpec, cols: string[]): GraderSpec {
  const clean = cols.filter(Boolean);
  if (clean.length <= 1) {
    const { key_fields: _drop, ...rest } = g;
    return { ...rest, key: clean[0] ?? "" };
  }
  return { ...g, key: "_key", key_fields: clean };
}

/** Every extract column a grader names — all must exist in golden_sql's output.
 *  For a ratio it's the numerator/denominator (value is *rebuilt*, so it need
 *  not exist); otherwise it's the value column. Mirrors the pack-lint's
 *  test_grader_columns_exist_in_golden_sql. */
export function namedColumns(g: GraderSpec): string[] {
  const cols = [...keyColumns(g)];
  if (g.aggregate === "ratio") {
    if (g.numerator) cols.push(g.numerator);
    if (g.denominator) cols.push(g.denominator);
  } else if (g.value) {
    cols.push(g.value);
  }
  return Array.from(new Set(cols.filter(Boolean)));
}

/** Deterministic pre-flight: the single reason this grader can't yet promote the
 *  golden to "ready", or null when it is dispatchable. Order matters — the first
 *  blocking reason is the one the curator should fix. */
export function graderIssue(g: GraderSpec, columns: string[]): string | null {
  const kind = g.kind;
  if (!kind) return "pick a grader kind";
  if (!GRADER_KINDS.includes(kind as GraderKind)) return `unknown kind: ${kind}`;

  const keys = keyColumns(g);
  if (kind === "row_set" || kind === "ranked_set" || kind === "series") {
    if (keys.length === 0) return `${kind} needs a key column`;
  }
  if (kind === "series" && !g.value) return "series needs a value column";

  if (g.aggregate === "ratio") {
    if (!g.numerator || !g.denominator) return "ratio needs a numerator and a denominator";
  } else if (g.aggregate === "sum" && !g.value) {
    return "sum needs a value column";
  }

  // Column existence — only enforceable once we know the extract's columns
  // (i.e. the SQL has been run). Until then the shape checks above still apply.
  if (columns.length) {
    const missing = namedColumns(g).find((c) => !columns.includes(c));
    if (missing) return `column "${missing}" is not in the extract — run ▶ Run SQL`;
  }
  return null;
}

/** Strip a spec down to the fields its kind/aggregate actually use, so the saved
 *  jsonb is minimal and never carries stale fields from an abandoned kind. */
export function pruneGrader(g: GraderSpec): GraderSpec {
  const kind = g.kind;
  if (!kind) return {};
  const out: GraderSpec = { kind };
  const keys = keyColumns(g);
  if (kind !== "scalar" && keys.length) {
    if (keys.length > 1) {
      out.key = "_key";
      out.key_fields = keys;
    } else {
      out.key = keys[0];
    }
  }
  if (kind === "ranked_set" && g.k) out.k = g.k;
  if ((kind === "scalar" || kind === "series") && g.tolerance_pct != null) {
    out.tolerance_pct = g.tolerance_pct;
  }
  if (g.aggregate) {
    out.aggregate = g.aggregate;
    if (g.aggregate === "ratio") {
      if (g.numerator) out.numerator = g.numerator;
      if (g.denominator) out.denominator = g.denominator;
    }
  }
  if ((kind === "series" || g.aggregate === "sum") && g.value) out.value = g.value;
  if (g.expected_objects?.length) out.expected_objects = g.expected_objects;
  return out;
}
