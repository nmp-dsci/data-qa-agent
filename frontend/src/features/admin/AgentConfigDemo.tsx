// Template Studio demo data — representative sample objects (the Hornsby
// rent-by-bedrooms worked example) composed into column-model pages. The
// preview renders them through the SAME PageLayout the chat report engine
// uses, so what the Studio shows is exactly what the agent's answers produce —
// and the generated Page object doubles as the "Contract JSON" the inspector
// exposes (what Data-Agent would have sent to render the page).
import type { AgentConfigEntry, Page, PageObject, PageObjectType, TemplateId } from "../../lib/api";
import { PageLayout } from "../../report-engine/PageLayout";
import { TEMPLATES } from "../../report-engine/registry";

// --- Sample data (deterministic, so previews are stable) -------------------

/** ~12-point rising sparkline series for the KPI tile. */
const SPARK = Array.from({ length: 12 }, (_, i) => ({ value: 600 + i * 6 + (i % 3) * 4 }));

/** 18 months × three bedroom series of median weekly rent for the Trend line. */
function trendRows(): Record<string, unknown>[] {
  const series: [string, number, number][] = [
    ["1 bd", 500, 2.2],
    ["2 bd", 620, 3.4],
    ["3 bd", 760, 4.1],
  ];
  const rows: Record<string, unknown>[] = [];
  for (const [name, base, slope] of series) {
    for (let m = 0; m < 18; m++) {
      const year = 2025 + Math.floor(m / 12);
      const month = (m % 12) + 1;
      const wobble = ((m * 7) % 5) - 2; // deterministic ±2 jitter
      rows.push({
        month: `${year}-${String(month).padStart(2, "0")}-01`,
        value: Math.round(base + slope * m + wobble),
        series: name,
      });
    }
  }
  return rows;
}

type Height = number | "sm" | "md" | "lg" | "fill";

const OBJ = {
  breakdown: (height: Height = "md"): PageObject => ({
    type: "breakdown",
    element_id: "demo:breakdown",
    role: "chart",
    data: {
      intent: "bar",
      dimension: "bedroom_band",
      measure: "median_rent",
      title: "Median weekly rent by bedrooms · Hornsby 2077",
      height,
      rows: [
        { bedroom_band: "1 bd", median_rent: 520, growth: 3.6 },
        { bedroom_band: "2 bd", median_rent: 671, growth: 6.1 },
        { bedroom_band: "3 bd", median_rent: 820, growth: 7.3 },
        { bedroom_band: "4 bd+", median_rent: 960, growth: 5.2 },
      ],
    },
  }),
  compare: (height: Height = "md"): PageObject => ({
    type: "compare",
    element_id: "demo:compare",
    role: "chart",
    data: {
      intent: "grouped-bar",
      dimension: "bedroom_band",
      measure: "median_rent",
      group: "property_type",
      title: "House vs unit median rent by bedrooms",
      height,
      rows: [
        { bedroom_band: "1 bd", property_type: "unit", median_rent: 520 },
        { bedroom_band: "1 bd", property_type: "house", median_rent: 560 },
        { bedroom_band: "2 bd", property_type: "unit", median_rent: 671 },
        { bedroom_band: "2 bd", property_type: "house", median_rent: 735 },
        { bedroom_band: "3 bd", property_type: "unit", median_rent: 820 },
        { bedroom_band: "3 bd", property_type: "house", median_rent: 910 },
      ],
    },
  }),
  kpi: (id = "demo:kpi", label = "2 bd unit median rent", value = "$671/wk", yoy = 0.061): PageObject => ({
    type: "kpi",
    element_id: id,
    role: "headline",
    data: {
      label,
      value,
      basis: "6-mo rolling · 2026-05",
      growth: { yoy },
      series: SPARK,
    },
  }),
  trend: (height: Height = "fill"): PageObject => ({
    type: "trend",
    element_id: "demo:trend",
    role: "chart",
    data: {
      intent: "line",
      x: "month",
      y: "value",
      series: "series",
      title: "Median weekly rent by month · postcode 2077",
      height,
      rows: trendRows(),
    },
  }),
  insight: (id: string, heading: string, text: string): PageObject => ({
    type: "insight",
    element_id: id,
    role: "insight",
    data: { heading, text, refs: ["Q1"] },
  }),
  note: (text: string): PageObject => ({
    type: "text",
    element_id: "demo:note",
    role: "note",
    data: { text },
  }),
};

/** A demo object of the given type — used by chart previews AND the playground. */
export function demoObjectOfType(type: PageObjectType, height: Height = "md"): PageObject {
  switch (type) {
    case "breakdown":
      return OBJ.breakdown(height);
    case "compare":
      return OBJ.compare(height);
    case "kpi":
      return OBJ.kpi();
    case "trend":
      return OBJ.trend(height);
    case "insight":
      return OBJ.insight(
        "demo:insight",
        "Drivers by bedroom band",
        "3 bd +7.3% > 2 bd +6.1% > 1 bd +3.6% — larger dwellings are appreciating fastest.",
      );
    case "text":
      return OBJ.note("2-bed units lead at +6.1% YoY; supply near the station stays tight.");
  }
}

/** The single demo object for a chart-registry entry, keyed by its object_type. */
export function chartDemoObject(entry: AgentConfigEntry): PageObject | null {
  const type = String(entry.spec["object_type"] ?? "") as PageObjectType;
  if (!["breakdown", "compare", "kpi", "trend", "insight", "text"].includes(type)) return null;
  return demoObjectOfType(type);
}

const INSIGHT_STEADY = () =>
  OBJ.insight("demo:i1", "Steady climb", "Median rent rose every quarter across all bedroom bands.");
const INSIGHT_GAP = () =>
  OBJ.insight("demo:i2", "Widening gap", "3-bed dwellings pulled ahead of 1-bed over the window.");

/** A full demo page (columns filled positionally) for a template entry. */
export function templateDemoPage(name: string): Page | null {
  const template = name as TemplateId;
  if (!(template in TEMPLATES)) return null;
  const pagesByTemplate: Record<TemplateId, Page> = {
    summary: {
      template: "summary",
      columns: [
        [
          OBJ.kpi(),
          OBJ.note("2-bed units lead at +6.1% YoY; supply near the station stays tight."),
        ],
        [OBJ.trend("fill")],
      ],
    },
    insights: {
      template: "insights",
      columns: [
        [
          OBJ.kpi("demo:t1", "1 bd", "$520/wk", 0.036),
          OBJ.kpi("demo:t2", "2 bd", "$671/wk", 0.061),
          OBJ.kpi("demo:t3", "3 bd", "$820/wk", 0.073),
          OBJ.insight(
            "demo:note",
            "Drivers by bedroom band",
            "3 bd +7.3% > 2 bd +6.1% > 1 bd +3.6% — larger dwellings are appreciating fastest.",
          ),
        ],
        [OBJ.breakdown("fill")],
      ],
    },
    "one-col": {
      template: "one-col",
      columns: [[OBJ.kpi(), OBJ.trend("md"), INSIGHT_STEADY(), INSIGHT_GAP()]],
    },
    "two-col": {
      template: "two-col",
      columns: [
        [OBJ.kpi(), INSIGHT_STEADY(), INSIGHT_GAP()],
        [OBJ.breakdown("fill")],
      ],
    },
    "three-col": {
      template: "three-col",
      columns: [
        [OBJ.kpi(), INSIGHT_GAP()],
        [OBJ.trend("fill")],
        [OBJ.breakdown("fill")],
      ],
    },
  };
  return pagesByTemplate[template];
}

/** The Page contract a selected registry entry previews (also shown as JSON). */
export function demoPageFor(entry: AgentConfigEntry, kind: "chart" | "template"): Page | null {
  if (kind === "template") return templateDemoPage(entry.name);
  const obj = chartDemoObject(entry);
  return obj ? { template: "one-col", columns: [[obj]] } : null;
}

/** Renders the demo visualisation for a selected registry entry — through the
 *  production PageLayout, so the preview IS what chat answers render. */
export function AgentConfigDemoPreview({
  entry,
  kind,
}: {
  entry: AgentConfigEntry;
  kind: "chart" | "template";
}) {
  const page = demoPageFor(entry, kind);
  if (!page) return <p className="muted">No preview available for this entry.</p>;
  return (
    <div className="answer-page">
      <PageLayout page={page} />
    </div>
  );
}
