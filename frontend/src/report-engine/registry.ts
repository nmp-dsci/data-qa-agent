// The published template registry — the frontend owns these layouts; the agent
// may only reference their ids and fill their regions. Kept in sync with the
// app.agent_config seed (migration 0014) and agent/pages.py TEMPLATE_IDS.
import type { Page } from "../lib/api";

export interface TemplateDef {
  id: Page["template"];
  label: string;
  /** Region render order. Unknown regions append after these. */
  regions: string[];
  /** two-col places the chart region beside the note/insight regions. */
  layout: "one-col" | "two-col";
}

export const TEMPLATES: Record<Page["template"], TemplateDef> = {
  summary: {
    id: "summary",
    label: "Summary",
    regions: ["hero", "chart", "note"],
    layout: "one-col",
  },
  insights: {
    id: "insights",
    label: "Insights",
    regions: ["chart", "tiles", "note"],
    layout: "one-col",
  },
  "one-col": {
    id: "one-col",
    label: "Report",
    regions: ["headline", "chart", "insights"],
    layout: "one-col",
  },
  "two-col": {
    id: "two-col",
    label: "Report",
    regions: ["headline", "chart", "insights"],
    layout: "two-col",
  },
};

export function templateFor(page: Page): TemplateDef {
  return TEMPLATES[page.template] ?? TEMPLATES["one-col"];
}
