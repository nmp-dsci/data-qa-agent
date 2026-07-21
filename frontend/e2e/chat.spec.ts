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

  // The summary page leads the answer, so it is the first .page-cols to land
  // (the agent may take minutes on a live LLM).
  //
  // Not [data-template="summary"]: `data-template` carries a TemplateId, and
  // since the s08 column model those are one-col / two-col / three-col. Page
  // *kind* (summary / insights) is a separate axis and never appears here, so
  // the old selector matched nothing and this test could only time out. The
  // agent was never the problem — a live run lands both pages in ~60s.
  const pageCols = page.locator(".page-cols").first();
  await expect(pageCols).toBeVisible({ timeout: 300_000 });

  // Column model in the wild: a registered column count, a KPI headline and a
  // chart svg.
  const colCount = Number(await pageCols.getAttribute("data-col-count"));
  expect(colCount).toBeGreaterThanOrEqual(1);
  expect(colCount).toBeLessThanOrEqual(3);
  expect(["one-col", "two-col", "three-col"]).toContain(
    await pageCols.getAttribute("data-template"),
  );
  await expect(pageCols.locator('[data-object-type="kpi"]').first()).toBeVisible();
  await expect(pageCols.locator("svg").first()).toBeVisible();
});
