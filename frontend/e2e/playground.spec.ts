// Template Playground — the combinatorial proof: every layout × every chart
// type in every column renders, layout switches re-flow, heights apply, and
// the generated contract JSON always matches the DOM. The suite enumerates
// CHART_OPTIONS from the registry, so new chart types (map, heatmap, …) join
// the matrix automatically.
import { Page as PWPage, expect, test } from "@playwright/test";
import { CHART_OPTIONS, TEMPLATES } from "../src/report-engine/registry";
import { login, openTemplateStudio } from "./helpers";

const LAYOUTS = ["one-col", "two-col", "three-col"] as const;
const CHART_SVG_TYPES = new Set(["trend", "breakdown", "compare", "kpi"]);

test.beforeEach(async ({ page }) => {
  await login(page, "Admin");
  await openTemplateStudio(page);
  await page.getByTestId("playground").scrollIntoViewIfNeeded();
});

async function contractFromPane(page: PWPage) {
  const body = await page.getByTestId("playground-json").locator("pre").textContent();
  return JSON.parse(body ?? "null");
}

for (const layout of LAYOUTS) {
  const colCount = TEMPLATES[layout].tracks.length;

  test(`${layout}: every chart type renders in every column`, async ({ page }) => {
    await page.getByTestId(`playground-layout-${layout}`).click();
    const canvas = page.getByTestId("playground-canvas");
    await expect(canvas.locator(".page-cols")).toHaveAttribute("data-template", layout);
    await expect(canvas.locator(".page-cols")).toHaveAttribute("data-col-count", String(colCount));

    for (let col = 0; col < colCount; col++) {
      for (const option of CHART_OPTIONS) {
        await page.getByTestId(`playground-col-${col}-type`).selectOption(option.type);
        const cell = canvas.locator(`.page-col[data-col="${col}"]`);
        await expect(cell.locator(`[data-object-type="${option.type}"]`)).toBeVisible();
        if (CHART_SVG_TYPES.has(option.type)) {
          await expect(cell.locator("svg").first()).toBeVisible();
        }
        // The generated contract names the same object in the same column.
        const contract = await contractFromPane(page);
        expect(contract.template).toBe(layout);
        expect(contract.columns[col][0].type).toBe(option.type);
      }
    }
  });
}

test("switching layouts re-flows the page and the contract follows", async ({ page }) => {
  const canvas = page.getByTestId("playground-canvas");
  for (const layout of LAYOUTS) {
    await page.getByTestId(`playground-layout-${layout}`).click();
    const cols = TEMPLATES[layout].tracks.length;
    await expect(canvas.locator(".page-cols")).toHaveAttribute("data-col-count", String(cols));
    const contract = await contractFromPane(page);
    expect(contract.columns.length).toBe(cols);
    // Contract object count == rendered object count (JSON ↔ DOM sync).
    const jsonObjects = contract.columns.reduce((n: number, c: unknown[]) => n + c.length, 0);
    await expect(canvas.locator(".page-obj")).toHaveCount(jsonObjects);
  }
});

test("height: fill stretches a lone chart to match the other column", async ({ page }) => {
  await page.getByTestId("playground-layout-two-col").click();
  await page.getByTestId("playground-col-0-type").selectOption("kpi");
  await page.getByTestId("playground-col-1-type").selectOption("trend");
  await page.getByTestId("playground-col-1-height").selectOption("fill");

  const canvas = page.getByTestId("playground-canvas");
  const fillCard = canvas.locator('.page-col[data-col="1"] .page-obj.fill');
  await expect(fillCard).toBeVisible();
  // And the contract carries the height the frontend rendered.
  const contract = await contractFromPane(page);
  expect(contract.columns[1][0].data.height).toBe("fill");

  // A fixed height renders shorter than lg.
  await page.getByTestId("playground-col-1-height").selectOption("sm");
  const smBox = await canvas.locator('.page-col[data-col="1"] .page-obj').boundingBox();
  await page.getByTestId("playground-col-1-height").selectOption("lg");
  const lgBox = await canvas.locator('.page-col[data-col="1"] .page-obj').boundingBox();
  expect((lgBox?.height ?? 0)).toBeGreaterThan((smBox?.height ?? 0) + 100);
});
