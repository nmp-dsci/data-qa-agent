// Explore E2E (s19): drives the live stack as a granted user would — pick a
// dataset, run each of the three tools, and assert real, DB-computed output.
// User One holds nsw_sales / nsw_rent / nsw_yield; the numbers come from the
// committed sample the pipeline builds, so this is a stable regression (no LLM:
// the Ask-AI box uses the deterministic offline interpreter).
import { expect, test } from "@playwright/test";
import { login } from "./helpers";

test("Explore: profile, trends and dictionary over the granted datasets", async ({ page }) => {
  test.setTimeout(120_000);
  await login(page, "User One");

  // --- Explore tab --------------------------------------------------------
  await page.getByRole("tab", { name: "Explore" }).click();
  const datasetPicker = page.getByLabel("Dataset");
  await expect(datasetPicker).toBeVisible({ timeout: 30_000 });

  // Granted datasets are listed (sales / rent / yield).
  await expect(datasetPicker.locator("option")).toHaveCount(3);
  await datasetPicker.selectOption({ label: "NSW rental bonds" });

  // --- Profile tool: FY cohort comparison --------------------------------
  // Use the Ask-AI box to populate the cohorts (deterministic offline stub),
  // then Run — exactly the reviewed flow (prefill, user runs).
  await page
    .getByLabel("Describe what to show")
    .fill("compare 2023 and 2022 weekly rent for houses");
  // "Set up" fetches /explore/ask asynchronously, then fills the controls. Wait
  // for the interpreted year filter to actually land as a chip before running —
  // otherwise Run fires against empty cohorts and the two sides come back equal.
  const askDone = page.waitForResponse((r) => r.url().includes("/explore/ask"));
  await page.getByRole("button", { name: "Set up" }).click();
  await askDone;
  await expect(page.locator(".ex-chip", { hasText: "Calendar year" }).first()).toBeVisible();
  await page.getByRole("button", { name: "Run profile" }).click();

  // Topline tiles render real numbers. The cohorts are now named by the filter
  // that distinguishes them (2023 vs 2022, houses shared), not "Target"/"Comparison".
  // Scoped to the KPI cards — the page headline ("2023 vs 2022 · …") would
  // otherwise substring-match too.
  const kpiCards = page.locator('[data-object-type="kpi"]');
  await expect(kpiCards.filter({ hasText: "2023 · Avg weekly rent" })).toBeVisible({
    timeout: 30_000,
  });
  await expect(kpiCards.filter({ hasText: "2022 · Avg weekly rent" })).toBeVisible();
  await expect(page.getByText("Data group comparison · all metrics")).toBeVisible();
  await expect(page.getByText("Positive uplifts · ranked")).toBeVisible();
  await expect(page.getByText("Per-predictor comparison · strongest signal first")).toBeVisible();

  // s20: the whole result is report-engine page objects — the same typed cards
  // chat answers and goldens render. Three KPI tiles, four tables (comparison,
  // filters, ± uplifts), per-predictor breakdown charts.
  await expect(page.locator('[data-object-type="kpi"]')).toHaveCount(3);
  await expect(page.locator('[data-object-type="table"]')).toHaveCount(4);
  expect(await page.locator('[data-object-type="breakdown"]').count()).toBeGreaterThan(0);

  // --- Trends tool: two chart apps ---------------------------------------
  await page.getByRole("tab", { name: "Trends" }).click();
  // Both chart apps autorun on open; their headings are present.
  await expect(page.getByText("trend 1")).toBeVisible();
  await expect(page.getByText("trend 2")).toBeVisible();

  // --- Data Dictionary tool: dictionary + extract ------------------------
  await page.getByRole("tab", { name: "Data Dictionary" }).click();
  await expect(page.getByText("Columns & values · from the manifest")).toBeVisible();
  await page.getByRole("button", { name: "Preview" }).click();
  await expect(page.getByRole("button", { name: /Download CSV/ })).toBeEnabled({ timeout: 30_000 });
});

// Regression for two real Profile bugs seen together on a postcode comparison:
//   1. multi-select (array) filters — postcode ["2077"] — rendered BLANK in the
//      "Data filters applied" table (an array is `typeof "object"`, so it hit the
//      range branch and lost its value).
//   2. it was easy to end up staring at two identical cohorts and not notice.
// This drives the postcode typeahead (the array shape) and asserts the filters
// render AND the two cohorts are genuinely different data.
test("Explore Profile: multi-select filters render and the cohorts differ", async ({ page }) => {
  test.setTimeout(120_000);
  await login(page, "User One");

  await page.getByRole("tab", { name: "Explore" }).click();
  const datasetPicker = page.getByLabel("Dataset");
  await expect(datasetPicker).toBeVisible({ timeout: 30_000 });
  await datasetPicker.selectOption({ label: "NSW property sales" });

  // Add a Postcode filter to one cohort via the typeahead popover — this stores an
  // ARRAY value (["2077"]), the exact shape that used to render blank. Comparison
  // first so its edit marks it "touched" (no prior-period auto-mirror onto target).
  async function setPostcode(tone: "target" | "comparison", code: string) {
    const row = page.locator(`.ex-filters.tone-${tone}`);
    await row.getByLabel("Add filter").selectOption("postcode");
    const trigger = row.getByRole("button", { name: "Postcode values" });
    await trigger.click();
    await row.locator(".ex-multi-search").fill(code);
    await row.locator(".ex-multi-opt", { hasText: code }).first().click();
    await trigger.click(); // close the popover
  }
  await setPostcode("comparison", "2076");
  await setPostcode("target", "2077");

  await page.getByRole("button", { name: "Run profile" }).click();

  // (1) The "Data filters applied" table shows the postcode row with BOTH values —
  //     not blank. This is the core regression assertion. (s20: the table is a
  //     report-engine `table` page object now, so target it by object type.)
  const filtersCard = page.locator('[data-object-type="table"]', {
    hasText: "Data filters applied",
  });
  await expect(filtersCard).toBeVisible({ timeout: 30_000 });
  const postcodeRow = filtersCard.locator("tr", { hasText: "Postcode" });
  await expect(postcodeRow).toContainText("2077");
  await expect(postcodeRow).toContainText("2076");

  // (2) The two cohorts are different data — the whole point of a comparison. If
  //     the filters silently didn't apply, both columns would be identical. The
  //     cohorts are named by the distinguishing postcode, so the KPI tiles read
  //     "2077 · …" / "2076 · …" (KPITile objects since s20).
  const totalValue = (code: string) =>
    page
      .locator('[data-object-type="kpi"]', { hasText: `${code} ·` })
      .locator(".h-value")
      .innerText();
  const targetTotal = (await totalValue("2077")).trim();
  const comparisonTotal = (await totalValue("2076")).trim();
  expect(targetTotal).not.toBe(comparisonTotal);
  // And the uplift is not a flat zero / em-dash (identical-cohort tell).
  const uplift = page.locator('[data-object-type="kpi"]', { hasText: "Uplift" });
  await expect(uplift.locator(".h-value")).not.toHaveText(/^(0|—)/);
});
