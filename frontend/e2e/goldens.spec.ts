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
import { login, pickOption } from "./helpers";

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
  // The structured builder is the primary (always-visible) panel in the ② Sandbox
  // section — grain/x are grain-driven checkbox lists from the dataset's typed
  // vocabulary (s28), group is a dropdown of the grain. Not free text.
  await page.getByTestId("builder-name").fill(OBJECT);
  await pickOption(page, page.getByTestId("builder-type"), "compare");
  const grain = page.getByTestId("builder-grain");
  // The checkbox LABELS are the dataset vocabulary's display names ("Month",
  // "Land size band"), not the raw column names — the builder has labelled them
  // since s28. Values below (builder-group, bar/line sources, enc-*) are still
  // addressed by raw column name, because those are picked by data-value.
  for (const c of ["Month", "Suburb", "Land size band"]) {
    await grain.getByLabel(c, { exact: true }).check();
  }
  // x / dimension (checkboxes from grain) and group (dropdown of grain).
  await page.getByTestId("builder-dimension").getByLabel("Land size band", { exact: true }).check();
  await pickOption(page, page.getByTestId("builder-group"), "suburb");
  await page.getByTestId("builder-filter").fill(FILTER);
  // bars = sum(n_sold) as sales_volume; line = wtd-avg total_sale_value / n_sold.
  await page.getByTestId("builder-bar-label").fill("sales_volume");
  await pickOption(page, page.getByTestId("builder-bar-source"), "n_sold");
  await page.getByTestId("builder-line-label").fill("avg_sale_price");
  await pickOption(page, page.getByTestId("builder-line-num"), "total_sale_value");
  await pickOption(page, page.getByTestId("builder-line-den"), "n_sold");
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
  await pickOption(page, page.getByTestId("page-template-1"), "two-col");
  const col = page.getByTestId("col-1-1"); // page 2 (index 1), column 2 (index 1)
  await expect(col).toBeVisible();

  // Visual object picker: open it, then pick "Line + bar chart" (type "compare").
  await page.getByTestId("add-btn-1-1").click();
  await page.getByTestId("add-opt-1-1-compare").click();

  // Open the new card's edit panel (the last ✎ in this column) and link it.
  await col.getByTitle("edit fields").last().click();
  await pickOption(page, col.getByTestId("linked-object-select"), OBJECT_ID);

  // Configure the chart: x = area_band, bars = sales_volume, line = avg_sale_price,
  // grouped by suburb — the columns come from the linked object's rows.
  const linked = page.locator(`[id="${OBJECT_ID}"]`);
  await pickOption(page, linked.getByTestId("enc-dimension"), "area_band");
  await pickOption(page, linked.getByTestId("enc-measure"), "sales_volume");
  await pickOption(page, linked.getByTestId("enc-line_measure"), "avg_sale_price");
  await pickOption(page, linked.getByTestId("enc-group"), "suburb");

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
