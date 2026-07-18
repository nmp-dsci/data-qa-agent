// Chat → Golden promotion E2E (s21): THE primary acceptance test for the
// "save as golden" feature. Drives the live stack exactly as an admin curator
// would, entirely through the frontend — no API shortcuts:
//
//   1. sign in as Admin, ask a real question in Chat
//   2. wait for the answer to publish, then press ★ save as golden
//   3. the app lands on the Golden Examples tab with the promoted DRAFT loaded —
//      question + ① SQL pre-filled from the run's captured artifacts (no re-run)
//   4. edit the question (a curator correction) and flip draft → ready, then Save
//   5. clear the editor and re-open the golden from the list — a fresh DB read —
//      proving the edit + ready status persisted.
//
// The promotion itself is a pure copy of what the original ask already captured
// (query_runs + messages), so nothing re-runs on the click. Only step 2's live
// agent answer uses the LLM (generous timeout), same as chat.spec.ts.
import { expect, test } from "@playwright/test";
import { login } from "./helpers";

const QUESTION = "Give me rent trends for postcode 2077 vs 2076";

test("Chat → Golden: promote a chat answer, edit it, and save a golden", async ({ page }) => {
  test.setTimeout(360_000);
  await login(page, "Admin");

  // --- 1. Ask the live agent in Chat --------------------------------------
  await page.getByPlaceholder(/Ask about/).fill(QUESTION);
  await page.getByRole("button", { name: "Ask" }).click();

  // --- 2. Wait for the FINAL answer, then promote -------------------------
  // The ★ chip only renders on the finished answer (it needs the audited
  // run_id), so its appearance is our "answer published" signal.
  const promote = page.getByTestId("promote-golden");
  await expect(promote).toBeVisible({ timeout: 300_000 });
  await expect(promote).toHaveText(/save as golden/i);
  await promote.click();
  // The click copies the stored artifacts and hands off to the Goldens tab.
  await expect(promote).toHaveText(/saved|opening in Goldens/i);

  // --- 3. Landed on Golden Examples with the promoted draft loaded ---------
  const question = page.getByTestId("golden-question");
  await expect(question).toBeVisible({ timeout: 30_000 });
  await expect(question).toHaveValue(/rent trends for postcode 2077/i);
  // ① SQL was copied from the run's sql_text (not re-executed).
  await expect(page.getByTestId("golden-sql")).toHaveValue(/select/i);
  // It's a real draft row (has an id → the Save button reads "Save golden").
  await expect(page.getByTestId("golden-save")).toHaveText(/save golden/i);

  // --- 4. Curator edits the question and promotes draft → ready -----------
  const edited = `${QUESTION} — curated ${Date.now()}`;
  await question.fill(edited);
  await page.getByTestId("golden-status").selectOption("ready");
  await page.getByTestId("golden-save").click();
  await expect(page.getByText("Saved.")).toBeVisible({ timeout: 30_000 });

  // --- 5. Re-open from the list (a fresh getGolden DB read) ----------------
  // Clear the editor, then click the golden in the left list — this refetches
  // it from the database, so the edited question + ready status coming back
  // prove the promotion round-tripped and persisted.
  await page.getByRole("button", { name: "+ New" }).click();
  await expect(question).toHaveValue("");
  await page.getByRole("button", { name: new RegExp(escapeRegExp(edited)) }).first().click();
  await expect(question).toHaveValue(edited, { timeout: 30_000 });
  await expect(page.getByTestId("golden-status")).toHaveValue("ready");
  await expect(page.getByTestId("golden-sql")).toHaveValue(/select/i);
});

/** Escape a string for safe use inside a RegExp (the edited question has a `—`). */
function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
