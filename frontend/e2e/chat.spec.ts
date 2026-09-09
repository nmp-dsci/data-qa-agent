// Live-chat E2E (D4): ask the real agent a question through the chat UI and
// assert the answer arrives as an embedded Slides/Sheets artifact
// (presentation-handover, s46) — proving the data-agent → deck builder →
// frontend path end to end. Slow: the LLM composes SQL + builds the deck;
// generous timeout, no mocks.
import { expect, test } from "@playwright/test";
import { login } from "./helpers";

const QUESTION =
  "show me trend of sale price for houses for Normanhurst vs Hornsby for all time 2010 to 2026";

test("a chat answer renders as an embedded Slides artifact", async ({ page }) => {
  test.setTimeout(360_000);
  await login(page, "Admin");

  await page.getByPlaceholder(/Ask about/).fill(QUESTION);
  await page.getByRole("button", { name: "Ask" }).click();

  // The agent may take minutes on a live LLM; the deck embed is the last thing
  // to land once the presentation is built.
  const frame = page.locator(".artifact-frame iframe").first();
  await expect(frame).toBeVisible({ timeout: 300_000 });
  await expect(frame).toHaveAttribute("src", /.+/);

  // The two link-outs to the real Slides deck and its Sheets data.
  await expect(page.getByRole("link", { name: "Open in Slides" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Open the data" })).toBeVisible();
});
