// Golden "New object with AI" E2E (s22): the acceptance test for NL-first object
// authoring. Drives the live stack exactly as a curator would —
//
//   1. open Golden Examples, give the golden a question + a hand-written ① SQL
//      extract (deterministic base — no draft needed)
//   2. in the ✦ New object with AI panel, describe a line chart in one sentence
//   3. Build & add to report → the agent authors + sandbox-verifies the code
//   4. assert the object appears in ② AND is auto-placed into the ③ report
//      (sandbox-backed), then Save persists it.
//
// The object authoring uses the live LLM (scaffold_object), so this is a live-
// stack journey with a generous timeout; the rent-profile deterministic build is
// covered separately by the fast unit test test_rent_trend_object_colours_by_bedroom_band.
import { expect, test } from "@playwright/test";
import { login } from "./helpers";

const QUESTION = "how has house price moved by land-size band?";
// A valid sales extract at month × area_band grain with a recomposed price — the
// input the AI object is built from (kept explicit so the base is deterministic).
const SQL = [
  "SELECT month, area_band,",
  "  sum(n_sold) AS n_sold,",
  "  sum(total_sale_value) AS total_sale_value,",
  "  round((sum(total_sale_value) / NULLIF(sum(n_sold), 0))::numeric) AS avg_sale_price",
  "FROM marts.property_sales",
  "WHERE suburb IN ('Hornsby', 'Normanhurst')",
  "GROUP BY month, area_band",
  "ORDER BY month, area_band",
].join("\n");
const INSTRUCTION =
  "average sale price by month as a line chart, coloured by area band";

test("Golden AI object: describe a chart → built in ② and auto-placed in ③", async ({
  page,
}) => {
  test.setTimeout(300_000);
  await login(page, "Admin");

  await page.getByRole("tab", { name: "Golden Examples" }).click();
  await page.getByTestId("golden-question").fill(QUESTION);
  await page.getByTestId("golden-sql").fill(SQL);

  // --- ✦ New object with AI: one sentence, one click -----------------------
  const ai = page.getByTestId("ai-object-instruction");
  await expect(ai).toBeVisible();
  await ai.fill(INSTRUCTION);
  // The type is auto-guessed from the words ("line chart" / "by month" → trend)
  // and the name is auto-derived from the sentence.
  await expect(page.getByTestId("ai-object-type")).toHaveValue("trend");
  await expect(page.getByTestId("ai-object-name")).not.toHaveValue("");

  await page.getByTestId("ai-object-build").click();

  // Success message confirms the object was built AND placed into the report.
  // The copy is "…and added it to the report", so the pattern has to allow the
  // object pronoun — without it this assertion could never match and the test
  // only ever failed after burning its full 240s timeout.
  await expect(page.getByTestId("ai-object-msg")).toContainText(/added it to the report/i, {
    timeout: 240_000,
  });

  // --- ② the object exists as a named sandbox object -----------------------
  const built = page.locator('[data-testid^="builtobj-obj:"]');
  await expect(built.first()).toBeVisible();

  // --- ③ it was auto-placed into the interactive report (sandbox-backed) ----
  const coverage = page.getByTestId("sandbox-coverage");
  await expect(coverage).toBeVisible();
  await expect(coverage).toContainText("100%");
  await expect(coverage).toContainText("✓");
  // The report chart renders real rows (not the empty-state).
  await expect(page.getByTestId("page-0")).toBeVisible();
  await expect(page.getByText("No chartable rows.")).toHaveCount(0);

  // --- Undo restores the pre-build state, then re-build & save -------------
  await page.getByTestId("ai-object-undo").click();
  await expect(page.getByTestId("sandbox-coverage")).toHaveCount(0); // report emptied
  await expect(built).toHaveCount(0);

  await page.getByTestId("ai-object-instruction").fill(INSTRUCTION);
  await page.getByTestId("ai-object-build").click();
  await expect(page.getByTestId("ai-object-msg")).toContainText(/added it to the report/i, {
    timeout: 240_000,
  });

  await page.getByRole("button", { name: /Submit presentation/ }).click();
  await page.getByRole("button", { name: /Create golden|Save golden/ }).click();
  await expect(page.getByText(/Created\.|Saved\./)).toBeVisible({ timeout: 30_000 });
});
