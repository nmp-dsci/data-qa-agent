// The published template + chart registry — the single frontend source for the
// s08 column model. The agent may only reference these template ids and fill
// their columns; placement is positional (columns[i][j]) so the renderer can
// never disagree with the contract. Kept in sync with the app.agent_config
// seed (migration 0015) and agent/pages.py TEMPLATE_IDS/TEMPLATE_COLUMNS —
// asserted by the contract tests.
import type { Page, PageObjectType, TemplateId } from "../lib/api";

export interface TemplateDef {
  id: TemplateId;
  label: string;
  /** CSS grid track per column, left→right. Length = max columns. */
  tracks: string[];
}

export const TEMPLATES: Record<TemplateId, TemplateDef> = {
  "one-col": {
    id: "one-col",
    label: "Report",
    tracks: ["minmax(0, 1fr)"],
  },
  "two-col": {
    id: "two-col",
    label: "Report",
    tracks: ["minmax(0, 0.9fr)", "minmax(0, 1.1fr)"],
  },
  "three-col": {
    id: "three-col",
    label: "Report",
    tracks: ["minmax(0, 1fr)", "minmax(0, 1fr)", "minmax(0, 1fr)"],
  },
};

export function templateFor(page: Page): TemplateDef {
  return TEMPLATES[page.template] ?? TEMPLATES["one-col"];
}

/** The CSS grid tracks for a page's columns, honouring an optional per-page
 *  `widths` override (relative fr weights, one per column) and falling back to
 *  the template's default tracks. Returns one track per template column
 *  (left→right) so both the renderer and the editor can index into it. A
 *  non-positive / missing weight keeps that column's template default. */
export function columnTracks(page: Page): string[] {
  const tracks = templateFor(page).tracks;
  const widths = page.widths;
  if (!Array.isArray(widths) || widths.length === 0) return tracks;
  return tracks.map((track, i) => {
    const w = widths[i];
    return typeof w === "number" && Number.isFinite(w) && w > 0 ? `minmax(0, ${w}fr)` : track;
  });
}

/** Plain-English name per object type — what the object *is* on screen — so the
 *  Report editor reads to a curator at a glance instead of the internal ids. */
export const OBJECT_TYPE_LABELS: Record<PageObjectType, string> = {
  kpi: "Tile",
  trend: "Line chart",
  breakdown: "Bar chart",
  compare: "Line + bar chart",
  insight: "Insight",
  text: "Text",
};

/** One-line "what it is" per object type — the visual object picker reads these
 *  so a curator chooses by recognition instead of decoding a bare dropdown. */
export const OBJECT_TYPE_DESCRIPTIONS: Record<PageObjectType, string> = {
  kpi: "Headline metric — big number + change",
  trend: "A trend over time",
  breakdown: "Compare categories",
  compare: "Two measures overlaid",
  insight: "Written takeaway + query refs",
  text: "A note or caption",
};

/** Semantic chart heights (px). "fill" stretches to the column instead. */
export const HEIGHTS = { sm: 180, md: 280, lg: 400 } as const;

export type HeightSpec = number | keyof typeof HEIGHTS | "fill";

/** Resolve an object's data.height to a chart height prop. */
export function resolveHeight(raw: unknown): number | "fill" {
  if (typeof raw === "number" && Number.isFinite(raw)) {
    return Math.min(1200, Math.max(80, raw));
  }
  if (raw === "fill") return "fill";
  if (typeof raw === "string" && raw in HEIGHTS) return HEIGHTS[raw as keyof typeof HEIGHTS];
  return HEIGHTS.md;
}

/** The chart library the playground + tests enumerate — one entry per object
 *  type the report engine can render. Adding a chart type here makes it appear
 *  in the Template Studio playground and the Playwright matrix automatically. */
export interface ChartOption {
  type: PageObjectType;
  intent: string;
  label: string;
}

export const CHART_OPTIONS: ChartOption[] = [
  { type: "kpi", intent: "kpi", label: "kpi (tiles)" },
  { type: "trend", intent: "line", label: "line (trend)" },
  { type: "breakdown", intent: "bar", label: "bar (breakdown)" },
  { type: "compare", intent: "grouped-bar", label: "grouped bar (compare)" },
  { type: "insight", intent: "insight", label: "insight (note)" },
];
