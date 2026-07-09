// Live-chat E2E (D4): ask the real agent a question through the chat UI and
// assert the answer arrives as a column-model page rendered by the report
// engine — proving the data-agent → contract → frontend path end to end.
// Slow: the LLM composes SQL + report; generous timeout, no mocks.
import { expect, test } from "@playwright/test";
import { login } from "./helpers";

const QUESTION =
  "show me trend of sale price for houses for Normanhurst vs Hornsby for all time 2010 to 2026";

test("a chat answer renders as column-model pages", async ({ page }) => {
  test.setTimeout(360_000);
  await login(page, "Admin");

  await page.getByPlaceholder(/Ask about/).fill(QUESTION);
  await page.getByRole("button", { name: "Ask" }).click();

  // The summary page leads the answer (agent may take minutes on live LLM).
  const pageCols = page.locator('.page-cols[data-template="summary"]');
  await expect(pageCols).toBeVisible({ timeout: 300_000 });

  // Column model in the wild: >= 1 column, a KPI headline and a chart svg.
  const colCount = Number(await pageCols.getAttribute("data-col-count"));
  expect(colCount).toBeGreaterThanOrEqual(1);
  expect(colCount).toBeLessThanOrEqual(2);
  await expect(pageCols.locator('[data-object-type="kpi"]').first()).toBeVisible();
  await expect(pageCols.locator("svg").first()).toBeVisible();
});
