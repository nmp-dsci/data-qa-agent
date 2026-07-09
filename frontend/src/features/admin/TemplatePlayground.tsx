// Template Playground — build a page from a contract, by hand. Pick a layout
// (one/two/three-col), then a chart type + height per column; the playground
// generates the exact Page JSON the data-agent would emit and renders it
// through the production PageLayout. This is the admin's (and the Playwright
// suite's) proof that any layout × any chart-per-column renders — the same
// interface a human uses to check it.
import { useMemo, useState } from "react";
import type { Page, PageObjectType, TemplateId } from "../../lib/api";
import { PageLayout } from "../../report-engine/PageLayout";
import { CHART_OPTIONS, TEMPLATES } from "../../report-engine/registry";
import { demoObjectOfType } from "./AgentConfigDemo";
import { ContractJson } from "../../ui/ContractJson";

const LAYOUTS: TemplateId[] = ["one-col", "two-col", "three-col"];
const HEIGHT_CHOICES = ["sm", "md", "lg", "fill"] as const;
type HeightChoice = (typeof HEIGHT_CHOICES)[number];

interface ColumnSpec {
  type: PageObjectType;
  height: HeightChoice;
}

const DEFAULT_COLS: ColumnSpec[] = [
  { type: "kpi", height: "md" },
  { type: "trend", height: "fill" },
  { type: "breakdown", height: "md" },
];

export function TemplatePlayground() {
  const [layout, setLayout] = useState<TemplateId>("two-col");
  const [cols, setCols] = useState<ColumnSpec[]>(DEFAULT_COLS);

  const colCount = TEMPLATES[layout].tracks.length;
  const page: Page = useMemo(
    () => ({
      template: layout,
      columns: cols.slice(0, colCount).map((c, i) => {
        const obj = demoObjectOfType(c.type, c.height);
        return [{ ...obj, element_id: `playground:${i}:${c.type}` }];
      }),
    }),
    [layout, cols, colCount],
  );

  const setCol = (i: number, patch: Partial<ColumnSpec>) =>
    setCols((prev) => prev.map((c, j) => (j === i ? { ...c, ...patch } : c)));

  return (
    <section data-testid="playground">
      <h3>Template Playground</h3>
      <p className="muted">
        Build a page from a contract: pick a layout, then what goes in each column. The generated
        JSON below is exactly what the data-agent would send the frontend to render this page.
      </p>

      <div className="playground-controls">
        <div className="playground-row">
          <span className="playground-label">Layout</span>
          <div className="seg" role="group" aria-label="page layout">
            {LAYOUTS.map((l) => (
              <button
                key={l}
                data-testid={`playground-layout-${l}`}
                className={layout === l ? "on" : ""}
                onClick={() => setLayout(l)}
              >
                {l}
              </button>
            ))}
          </div>
        </div>
        {Array.from({ length: colCount }, (_, i) => (
          <div className="playground-row" key={i}>
            <span className="playground-label">Column {i + 1}</span>
            <select
              data-testid={`playground-col-${i}-type`}
              value={cols[i].type}
              onChange={(e) => setCol(i, { type: e.target.value as PageObjectType })}
            >
              {CHART_OPTIONS.map((o) => (
                <option key={o.type} value={o.type}>
                  {o.label}
                </option>
              ))}
            </select>
            <select
              data-testid={`playground-col-${i}-height`}
              value={cols[i].height}
              onChange={(e) => setCol(i, { height: e.target.value as HeightChoice })}
              title="chart height (fill stretches to the column)"
            >
              {HEIGHT_CHOICES.map((h) => (
                <option key={h} value={h}>
                  height: {h}
                </option>
              ))}
            </select>
          </div>
        ))}
      </div>

      <div className="agent-demo-canvas report" data-testid="playground-canvas">
        <div className="answer-page">
          <PageLayout page={page} />
        </div>
      </div>

      <ContractJson page={page} testId="playground-json" defaultOpen />
    </section>
  );
}
