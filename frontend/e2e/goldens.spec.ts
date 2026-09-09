// Golden Examples E2E (s46): the acceptance test for manual golden authoring
// after the Presentation Object builder / ReportEditor were retired
// (presentation-handover — Slides/Sheets are the report now, so a golden is
// just a question + its SQL extract + a grader spec). Drives the live stack
// exactly as a curator would: create a golden from scratch, run its SQL, pick
// a grader, save, confirm it round-trips, then delete it.
//
// Promoting + editing an EXISTING golden (from a chat answer) is covered by
// chat-to-golden.spec.ts; this spec covers the from-scratch create/list/delete
// path that one doesn't exercise.
import { expect, test } from "@playwright/test";
import { login, pickOption } from "./helpers";

const QUESTION = `Golden Examples e2e ${Date.now()}`;
const SQL = "select suburb, count(*) as n from marts.property_sales group by suburb limit 5";

test("Golden Examples: create, run SQL, grade, save, reload and delete a golden", async ({ page }) => {
  test.setTimeout(120_000);
  await login(page, "Admin");

  await page.getByRole("tab", { name: "Golden Examples" }).click();
  await page.getByTestId("golden-new").click();

  const question = page.getByTestId("golden-question");
  await question.fill(QUESTION);
  await page.getByTestId("golden-sql").fill(SQL);

  // ▶ Run SQL against the live, RLS-scoped extract — the rows land as a plain
  // table (no chart, s46).
  await page.getByTestId("golden-run-sql").click();
  await expect(page.locator(".table-wrap")).toBeVisible({ timeout: 30_000 });

  // A dispatchable grader — scalar needs no key/value, so it's valid immediately.
  await pickOption(page, page.getByTestId("grader-kind"), "scalar");

  await page.getByTestId("golden-save").click();
  await expect(page.getByText("Saved.")).toBeVisible({ timeout: 30_000 });

  // The new golden appears in the list.
  const row = page.getByRole("button", { name: new RegExp(QUESTION) });
  await expect(row).toBeVisible({ timeout: 30_000 });

  // Reload it (a fresh getGolden DB read) and confirm it round-tripped.
  await page.getByTestId("golden-new").click();
  await expect(question).toHaveValue("");
  await row.click();
  await expect(question).toHaveValue(QUESTION, { timeout: 30_000 });
  await expect(page.getByTestId("golden-sql")).toHaveValue(/select/i);

  // Delete it and confirm it's gone from the list.
  await page.getByTestId("golden-delete").click();
  await expect(page.getByRole("button", { name: new RegExp(QUESTION) })).toHaveCount(0);
});
