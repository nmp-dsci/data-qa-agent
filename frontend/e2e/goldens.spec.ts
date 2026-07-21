// Golden Sandbox E2E (s18): the acceptance test for the Presentation Object
// builder. Drives the live stack exactly as a curator would —
//
//   1. draft the golden for "show house price growth for hornsby / normanhurst"
//   2. build a NAMED presentation object `line-bar-sale-volume` in the Sandbox:
//      unique per month/suburb/area_band, bars = sales volume (12-mo), line =
//      6-mo avg sale price, x = area_band, grouped by suburb
//   3. in the interactive report, add a "Line + bar chart" on page 2 / column 2,
//      link it to `line-bar-sale-volume`, and configure its encodings
//   4. assert the combo renders with real, sandbox-computed rows, then save.
//
// The object build is deterministic (no LLM), so this is a stable regression;
// only the first-pass agent draft uses the live LLM (generous timeout). No mocks.
import { expect, test } from "@playwright/test";
import { login } from "./helpers";

const QUESTION = "show house price growth for hornsby / normanhurst";
const OBJECT = "line-bar-sale-volume";
const OBJECT_ID = `obj:${OBJECT}`;
// Scope the object's extract deterministically (this question drafts a postcode
// growth ranking, so there's no suburb-name filter for the builder to carry).
const FILTER = "property_type = 'house' AND suburb IN ('Hornsby', 'Normanhurst')";

test("Golden Sandbox: build line-bar-sale-volume and wire it into the report", async ({ page }) => {
  // The budget has to exceed the sum of the allowances this test hands its own
  // steps: 360s for the live draft + 120s for the sandbox build + 30s save +
  // 30s reload = 540s of inner waits alone, before any interactive work. At the
  // previous 420s the test could not pass whenever the draft ran slow — it ran
  // out of budget mid-flow rather than failing on anything real.
  test.setTimeout(900_000);
  await login(page, "Admin");

  // --- Golden Examples tab ------------------------------------------------
  await page.getByRole("tab", { name: "Golden Examples" }).click();
  const question = page.getByPlaceholder(/Question/);
  await expect(question).toBeVisible();
  await question.fill(QUESTION);

  // --- 1. First-pass draft (live agent) -----------------------------------
  await page.getByRole("button", { name: /Draft with agent/ }).click();
  // The draft fills every stage; wait for the report editor to show pages.
  await expect(page.getByTestId("page-0")).toBeVisible({ timeout: 360_000 });

  // --- 2. Build the named presentation object -----------------------------
  // The structured builder lives in a <details> whose summary reads "advanced
  // — structured builder…". It was renamed and demoted under the AI panel in
  // s22 (d70d1dd) and this spec was never updated: "Presentation Object
  // builder" now survives only in a source comment, so the old locator matched
  // nothing in the DOM and the test could only ever time out here.
  await page.getByText(/structured builder/i).click(); // expand the builder
  await page.getByTestId("builder-name").fill(OBJECT);
  await page.getByTestId("builder-type").selectOption({ label: "Line + bar chart" });
  await page.getByTestId("builder-grain").fill("month, suburb, area_band");
  await page.getByTestId("builder-dimension").fill("area_band");
  await page.getByTestId("builder-group").fill("suburb");
  await page.getByTestId("builder-filter").fill(FILTER);
  // bars = sum(n_sold) as sales_volume; line = wtd-avg total_sale_value / n_sold.
  await page.getByTestId("builder-bar-label").fill("sales_volume");
  await page.getByTestId("builder-bar-source").fill("n_sold");
  await page.getByTestId("builder-line-label").fill("avg_sale_price");
  await page.getByTestId("builder-line-num").fill("total_sale_value");
  await page.getByTestId("builder-line-den").fill("n_sold");
  await page.getByTestId("builder-build").click();

  // The built object appears with real, sandbox-computed rows + its skills.
  const built = page.getByTestId(`builtobj-${OBJECT_ID}`);
  await expect(built).toBeVisible({ timeout: 120_000 });
  await built.locator("summary").first().click(); // expand the card
  await expect(built).toContainText("area_band");
  await expect(built).toContainText("Hornsby");
  await expect(built).toContainText("Normanhurst");
  // Skills applied: the maker chart skill + the windowed price metric ("6-mo").
  await expect(built).toContainText("dual_axis_chart");
  await expect(built).toContainText("6-mo");

  // --- 3. Add a Line + bar chart on page 2 / column 2, linked to the object ---
  // Ensure a two-column page 2 exists (the drafted insights page usually is).
  if ((await page.getByTestId("page-1").count()) === 0) {
    await page.getByRole("button", { name: /Add blank page/ }).last().click();
  }
  await page.getByTestId("page-template-1").selectOption("two-col");
  const col = page.getByTestId("col-1-1"); // page 2 (index 1), column 2 (index 1)
  await expect(col).toBeVisible();

  // Visual object picker: open it, then pick "Line + bar chart" (type "compare").
  await page.getByTestId("add-btn-1-1").click();
  await page.getByTestId("add-opt-1-1-compare").click();

  // Open the new card's edit panel (the last ✎ in this column) and link it.
  await col.getByTitle("edit fields").last().click();
  await col.getByTestId("linked-object-select").selectOption(OBJECT_ID);

  // Configure the chart: x = area_band, bars = sales_volume, line = avg_sale_price,
  // grouped by suburb — the columns come from the linked object's rows.
  const linked = page.locator(`[id="${OBJECT_ID}"]`);
  await linked.getByTestId("enc-dimension").selectOption("area_band");
  await linked.getByTestId("enc-measure").selectOption("sales_volume");
  await linked.getByTestId("enc-line_measure").selectOption("avg_sale_price");
  await linked.getByTestId("enc-group").selectOption("suburb");

  // --- 4. The combo renders with real rows (not the empty-state message) ----
  await expect(linked.locator('svg[role="img"]')).toBeVisible();
  await expect(linked.getByText("No chartable rows.")).toHaveCount(0);

  // Submit + save the golden; the object is now sandbox-backed in the report.
  await page.getByRole("button", { name: /Submit presentation/ }).click();
  await page.getByRole("button", { name: /Create golden|Save golden/ }).click();
  await expect(page.getByText(/Created\.|Saved\./)).toBeVisible({ timeout: 30_000 });

  // --- 5. Reload the saved golden (the load path) --------------------------
  // NON-NEGOTIABLE INVARIANT: every object in the ③ presentation must map to a
  // ② Sandbox object. After re-opening a saved golden the coverage must be 100%
  // and the named object must still be present in the Sandbox.
  await page.getByRole("button", { name: "+ New" }).click(); // clear the editor
  await page.getByRole("button", { name: new RegExp(QUESTION, "i") }).first().click();
  const coverage = page.getByTestId("sandbox-coverage");
  await expect(coverage).toBeVisible({ timeout: 30_000 });
  await expect(coverage).toContainText("100%");
  await expect(coverage).toContainText("✓");
  await expect(page.getByTestId(`builtobj-${OBJECT_ID}`)).toBeVisible();
});
