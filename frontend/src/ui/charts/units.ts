// Value units — what a number IS, so an axis, a tooltip and a cell all say the
// same thing about the same figure.
//
// The unit is normally DECLARED by whatever built the object (`data.unit`,
// `data.y_unit`, a table column's `format`), resolved server-side from the
// metric the number was aggregated from plus any derive applied on top — see
// services/data-agent/agent/units.py, whose COLUMN_UNITS this file mirrors
// (test_registry_sync.py asserts the two agree).
//
// `unitFromName` is the fallback for objects saved before units travelled with
// them. It reads the metric name, not a loose word match, because the old
// heuristic (/price|value|rent|…/) called every trend dollars: a trend frame's
// column is literally "value". An unrecognised name is a plain number — never
// currency, which would invent a dollar sign the data never claimed.

export type Unit = "currency" | "number" | "percent";

const UNITS: readonly string[] = ["currency", "number", "percent"];

/** Mart metric → unit. Mirrors the Explore manifest's per-metric `fmt`. */
const COLUMN_UNITS: Record<string, Unit> = {
  n_sold: "number",
  n_rented: "number",
  n_unit: "number",
  total_sale_value: "currency",
  total_weekly_rent: "currency",
  avg_sale_price: "currency",
  avg_weekly_rent: "currency",
  gross_yield_pct: "percent",
  pct_unit: "percent",
};

/** A derive can REPLACE the base unit — a growth on dollars is a percentage. */
const DERIVE_UNITS: Record<string, Unit> = {
  growth: "percent",
  yoy: "percent",
  share: "percent",
  index: "number",
  rank: "number",
};

const DERIVE_SUFFIX = /\s+(growth|yoy|share|index|rank|cumulative|rolling|latest)\s*%?$/i;
const PIVOT_SEP = " · ";

// Names outside the manifest still say what they are — an ad-hoc SQL-editor
// chart over `median_price`, an ops `pass_pct`. Matched as whole WORDS, not
// substrings: the old heuristic's /rent/ also fired on `n_rented`, a count.
const PERCENT_WORDS = ["pct", "percent", "share", "growth", "yoy", "rate"];
const MONEY_WORDS = [
  "price",
  "prices",
  "rent",
  "sale",
  "sales",
  "value",
  "values",
  "cost",
  "costs",
  "revenue",
];
// A column called just "value" is a placeholder, not a claim about money — it
// is what every trend frame's value column is called, which is how every trend
// (bond counts included) came to be drawn in dollars.
const PLACEHOLDER_NAMES = ["value", "val", "v", "y", "metric", "measure"];

/**
 * The unit of a metric column, or of a label derived from one. Resolution
 * order: the manifest's metrics, the derive a label carries, then the words in
 * the name. A name that says nothing is a plain number.
 */
export function unitFromName(field: string | null | undefined): Unit {
  const text = String(field ?? "").trim();
  if (!text) return "number";
  const sep = text.indexOf(PIVOT_SEP);
  if (sep > -1) {
    // A pivot cross-tab column ("<metric> · 2077"): the metric is the head,
    // except the Δ% gap column, a percentage whatever the metric was.
    const tail = text.slice(sep + PIVOT_SEP.length).trim();
    return tail.endsWith("%") ? "percent" : unitFromName(text.slice(0, sep));
  }
  const known = COLUMN_UNITS[text.toLowerCase()];
  if (known) return known;
  const suffix = DERIVE_SUFFIX.exec(text);
  if (suffix) {
    return DERIVE_UNITS[suffix[1].toLowerCase()] ?? unitFromName(text.slice(0, suffix.index));
  }
  const lower = text.toLowerCase();
  if (PLACEHOLDER_NAMES.includes(lower)) return "number";
  const words = new Set(lower.split(/[^a-z0-9%]+/).filter(Boolean));
  if (text.includes("%") || PERCENT_WORDS.some((w) => words.has(w))) return "percent";
  if (MONEY_WORDS.some((w) => words.has(w))) return "currency";
  return "number";
}

/** The declared unit when there is one, else what the metric's name says. */
export function unitFor(field: string | null | undefined, declared?: string | null): Unit {
  if (declared && UNITS.includes(declared)) return declared as Unit;
  return unitFromName(field);
}
