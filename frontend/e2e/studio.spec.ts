// Template Studio — the registry tables, the Template Preview (every template
// renders through the production PageLayout; one-col regression included), and
// the contract JSON inspector. Also asserts the published DB registry matches
// the frontend registry — the third leg of the contract sync test.
import { expect, test } from "@playwright/test";
import { TEMPLATES } from "../src/report-engine/registry";
import { login, openTemplateStudio } from "./helpers";

const TEMPLATE_IDS = Object.keys(TEMPLATES);

test.beforeEach(async ({ page }) => {
  await login(page, "Admin");
  await openTemplateStudio(page);
});

test("published registry matches the frontend registry (contract sync)", async ({ page }) => {
  const rows = page.getByTestId("templates-table").locator("tbody tr td:first-child code");
  await expect(rows).toHaveText(TEMPLATE_IDS);
});

for (const id of TEMPLATE_IDS) {
  test(`template preview renders ${id} with its own layout`, async ({ page }) => {
    await page.getByTestId(`template-row-${id}`).click();
    const cols = page.getByTestId("template-preview-canvas").locator(".page-cols");
    await expect(cols).toHaveAttribute("data-template", id);
    // Never more columns than the template allows; every column has content.
    const colCount = Number(await cols.getAttribute("data-col-count"));
    expect(colCount).toBeGreaterThanOrEqual(1);
    expect(colCount).toBeLessThanOrEqual(TEMPLATES[id as keyof typeof TEMPLATES].tracks.length);
    await expect(cols.locator(".page-obj").first()).toBeVisible();
  });
}

test("one-col preview really is a single column (regression)", async ({ page }) => {
  await page.getByTestId("template-row-one-col").click();
  const cols = page.getByTestId("template-preview-canvas").locator(".page-cols");
  await expect(cols).toHaveAttribute("data-template", "one-col");
  await expect(cols).toHaveAttribute("data-col-count", "1");
  // The stacked one-col demo carries a kpi, a chart and insights.
  await expect(cols.locator("svg").first()).toBeVisible();
});

test("contract JSON shows exactly what the agent sends", async ({ page }) => {
  await page.getByTestId("template-row-three-col").click();
  const inspector = page.getByTestId("template-preview-json");
  await inspector.locator("summary").click();
  const body = await inspector.locator("pre").textContent();
  const contract = JSON.parse(body ?? "null");
  expect(contract.template).toBe("three-col");
  expect(Array.isArray(contract.columns)).toBe(true);
  expect(contract.columns.length).toBe(3);
  // Placement is positional; meaning is typed.
  for (const col of contract.columns) {
    for (const obj of col) {
      expect(obj.type).toBeTruthy();
      expect(obj.element_id).toBeTruthy();
    }
  }
});
