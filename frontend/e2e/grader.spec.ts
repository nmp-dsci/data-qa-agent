// Grader-spec editor E2E (draft → ready promotion). Two deterministic, no-LLM
// acceptance checks that drive the live stack exactly as a curator would:
//
//   1. LOAD — the curated nsw_rent golden carries the one real authored grader (a
//      composite-key ratio series). Opening it must decode that jsonb back into
//      the dropdowns (kind=series, key=month+postcode, aggregate=ratio,
//      numerator/denominator) and read "ready — scoreable".
//   2. GATE — a fresh golden cannot be promoted until a grader kind is chosen.
//      Picking `scalar` (which needs no key/value) unblocks it, and Promote flips
//      the status to ready. This is the same gate the CI pack-lint enforces.
//
// No mutation of saved data and no LLM, so this is a stable regression.
import { expect, test } from "@playwright/test";
import { login } from "./helpers";

const CURATED = /rent trends for postcode 2077 vs 2076/i;

test("Grader editor: decodes a real grader and gates draft → ready", async ({ page }) => {
  test.setTimeout(120_000);
  await login(page, "Admin");
  await page.getByRole("tab", { name: "Golden Examples" }).click();

  // --- 1. LOAD: the curated rent golden's real composite-ratio-series grader ---
  await page.getByTestId("golden-dataset").selectOption("nsw_rent");
  await page.getByRole("button", { name: CURATED }).first().click();

  const editor = page.getByTestId("grader-editor");
  await expect(editor).toBeVisible();
  // The stored `key: "_key" + key_fields: [month, postcode]` decodes into the
  // key multi-select; ratio decodes into num/den; series into kind + value.
  await expect(page.getByTestId("grader-kind")).toHaveValue("series");
  await expect(page.getByTestId("grader-key")).toHaveValues(["month", "postcode"]);
  await expect(page.getByTestId("grader-aggregate")).toHaveValue("ratio");
  await expect(page.getByTestId("grader-value")).toHaveValue("avg_weekly_rent");
  await expect(page.getByTestId("grader-numerator")).toHaveValue("total_weekly_rent");
  await expect(page.getByTestId("grader-denominator")).toHaveValue("n_rented");
  await expect(page.getByTestId("grader-expected-objects")).toHaveValues(["trend"]);
  // A valid grader on a `ready` golden reads as scoreable, not blocked.
  await expect(page.getByTestId("grader-status")).toContainText("ready");
  // The left-list badge reflects the grader kind (from the grader_kind column).
  await expect(
    page.getByRole("button", { name: CURATED }).first().getByText("grader: series"),
  ).toBeVisible();

  // --- 2. GATE: a fresh golden is blocked until a kind is chosen ---------------
  await page.getByRole("button", { name: "+ New" }).click();
  await expect(page.getByTestId("grader-check")).toContainText("pick a grader kind");
  await expect(page.getByTestId("grader-promote")).toBeDisabled();

  // scalar needs no key/value, so choosing it is enough to make the golden
  // dispatchable — the check turns green and Promote enables.
  await page.getByTestId("grader-kind").selectOption("scalar");
  await expect(page.getByTestId("grader-check")).toContainText("ready to promote");
  await expect(page.getByTestId("grader-promote")).toBeEnabled();

  // Promote flips the status (both the panel line and the header dropdown).
  await page.getByTestId("grader-promote").click();
  await expect(page.getByTestId("grader-status")).toContainText("ready");
  await expect(page.getByTestId("golden-status")).toHaveValue("ready");
});
