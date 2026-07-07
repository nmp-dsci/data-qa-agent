// Live demo previews for the Agent-Config registry. Each chart/template entry
// is rendered with representative sample data (the Hornsby rent-by-bedrooms
// worked example) through the SAME production renderers the report engine uses
// (ObjectBody + the visx charts), so the preview is exactly what the agent
// would compose — an admin can click an entry and see its visualisation.
import type { AgentConfigEntry, Page, PageObject } from "../../lib/api";
import { ObjectBody } from "../../report-engine/PagesView";
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
      const year = 2025 + Math.floor((0 + m) / 12);
      const month = ((0 + m) % 12) + 1;
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

const OBJ = {
  breakdown: (): PageObject => ({
    type: "breakdown",
    element_id: "demo:breakdown",
    region: "chart",
    data: {
      dimension: "bedroom_band",
      measure: "median_rent",
      title: "Median weekly rent by bedrooms · Hornsby 2077",
      rows: [
        { bedroom_band: "1 bd", median_rent: 520, growth: 3.6 },
        { bedroom_band: "2 bd", median_rent: 671, growth: 6.1 },
        { bedroom_band: "3 bd", median_rent: 820, growth: 7.3 },
        { bedroom_band: "4 bd+", median_rent: 960, growth: 5.2 },
      ],
    },
  }),
  compare: (): PageObject => ({
    type: "compare",
    element_id: "demo:compare",
    region: "chart",
    data: {
      dimension: "bedroom_band",
      measure: "median_rent",
      group: "property_type",
      title: "House vs unit median rent by bedrooms",
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
  kpi: (region = "hero"): PageObject => ({
    type: "kpi",
    element_id: `demo:kpi:${region}`,
    region,
    data: {
      label: "2 bd unit median rent",
      value: "$671/wk",
      basis: "6-mo rolling · 2026-05",
      growth: { yoy: 0.061 },
      series: SPARK,
    },
  }),
  trend: (): PageObject => ({
    type: "trend",
    element_id: "demo:trend",
    region: "chart",
    data: {
      x: "month",
      y: "value",
      series: "series",
      title: "Median weekly rent by month · postcode 2077",
      rows: trendRows(),
    },
  }),
  insight: (id: string, region: string, heading: string, text: string): PageObject => ({
    type: "insight",
    element_id: id,
    region,
    data: { heading, text, refs: ["Q1"] },
  }),
};

/** The single demo object for a chart-registry entry, keyed by its object_type. */
export function chartDemoObject(entry: AgentConfigEntry): PageObject | null {
  const type = String(entry.spec["object_type"] ?? "");
  switch (type) {
    case "breakdown":
      return OBJ.breakdown();
    case "compare":
      return OBJ.compare();
    case "kpi":
      return OBJ.kpi("chart");
    case "trend":
      return OBJ.trend();
    default:
      return null;
  }
}

/** A KPI tile for the "tiles" region (per-bedroom-band summary numbers). */
function tile(id: string, label: string, value: string, yoy: number): PageObject {
  return {
    type: "kpi",
    element_id: id,
    region: "tiles",
    data: { label, value, basis: "6-mo rolling · 2026-05", growth: { yoy }, series: SPARK },
  };
}

/** A full demo page (objects filling the template's regions) for a template. */
export function templateDemoPage(entry: AgentConfigEntry): Page | null {
  const name = entry.name as Page["template"];
  const objectsByTemplate: Record<string, PageObject[]> = {
    summary: [
      OBJ.kpi("hero"),
      OBJ.trend(),
      OBJ.insight(
        "demo:note",
        "note",
        "What drives it",
        "2-bed units lead at +6.1% YoY; supply near the station stays tight.",
      ),
    ],
    insights: [
      OBJ.breakdown(),
      tile("demo:t1", "1 bd", "$520/wk", 0.036),
      tile("demo:t2", "2 bd", "$671/wk", 0.061),
      tile("demo:t3", "3 bd", "$820/wk", 0.073),
      OBJ.insight(
        "demo:note",
        "note",
        "Drivers by bedroom band",
        "3 bd +7.3% > 2 bd +6.1% > 1 bd +3.6% — larger dwellings are appreciating fastest.",
      ),
    ],
    "one-col": [
      OBJ.kpi("headline"),
      OBJ.trend(),
      OBJ.insight("demo:i1", "insights", "Steady climb", "Median rent rose every quarter across all bedroom bands."),
      OBJ.insight("demo:i2", "insights", "Widening gap", "3-bed dwellings pulled ahead of 1-bed over the window."),
    ],
    "two-col": [
      OBJ.kpi("headline"),
      OBJ.breakdown(),
      OBJ.insight("demo:i1", "insights", "Steady climb", "Median rent rose every quarter across all bedroom bands."),
      OBJ.insight("demo:i2", "insights", "Widening gap", "3-bed dwellings pulled ahead of 1-bed over the window."),
    ],
  };
  const objects = objectsByTemplate[name];
  if (!objects) return null;
  return { template: name, objects };
}

// --- Rendering (mirrors PagesView's PageSection layout, minus feedback) -----

function cardClass(o: PageObject): string {
  if (o.type === "kpi") return "h-tile page-obj";
  if (o.type === "insight" || o.type === "text") return "insight-card page-obj";
  return "chart-card page-obj";
}

function DemoObjectCard({ o }: { o: PageObject }) {
  return (
    <div className={cardClass(o)}>
      <ObjectBody o={o} />
    </div>
  );
}

function DemoRegion({ objs }: { objs: PageObject[] }) {
  const isHero = objs.every((o) => o.type === "kpi");
  return (
    <div className={isHero ? "headline-grid" : "page-region"}>
      {objs.map((o) => (
        <DemoObjectCard key={o.element_id} o={o} />
      ))}
    </div>
  );
}

function DemoPage({ page }: { page: Page }) {
  const template = TEMPLATES[page.template] ?? TEMPLATES["one-col"];
  const byRegion = new Map<string, PageObject[]>();
  for (const o of page.objects) {
    const arr = byRegion.get(o.region) ?? [];
    arr.push(o);
    byRegion.set(o.region, arr);
  }
  const orderedRegions = [
    ...template.regions.filter((r) => byRegion.has(r)),
    ...[...byRegion.keys()].filter((r) => !template.regions.includes(r)),
  ];
  const chartRegions = orderedRegions.filter((r) => r === "chart");
  const otherRegions = orderedRegions.filter((r) => r !== "chart");
  const region = (r: string) => <DemoRegion key={r} objs={byRegion.get(r) ?? []} />;

  if (template.layout === "two-col" && chartRegions.length > 0) {
    return (
      <div className="page-two-col">
        <div>{chartRegions.map(region)}</div>
        <div>{otherRegions.map(region)}</div>
      </div>
    );
  }
  return <>{orderedRegions.map(region)}</>;
}

/** Renders the demo visualisation for a selected registry entry. */
export function AgentConfigDemoPreview({
  entry,
  kind,
}: {
  entry: AgentConfigEntry;
  kind: "chart" | "template";
}) {
  if (kind === "chart") {
    const obj = chartDemoObject(entry);
    if (!obj) return <p className="muted">No preview available for this chart.</p>;
    return (
      <div className="answer-page">
        <DemoRegion objs={[obj]} />
      </div>
    );
  }
  const page = templateDemoPage(entry);
  if (!page) return <p className="muted">No preview available for this template.</p>;
  return (
    <div className="answer-page">
      <DemoPage page={page} />
    </div>
  );
}
