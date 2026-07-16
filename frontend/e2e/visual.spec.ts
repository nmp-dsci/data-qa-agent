// s13 Phase F: screenshot regression across the responsive matrix — the suite
// that would have caught the composer-below-the-fold and mobile-squeeze bugs.
// Baselines are per-platform (Playwright suffixes -darwin/-linux); regenerate
// after intentional visual changes with:
//   npx playwright test visual --update-snapshots
// Masks cover content that legitimately varies between runs (conversation
// history, time-of-day greeting, agent memories).
import { expect, test } from "@playwright/test";
import { login } from "./helpers";

const TIERS = [
  { name: "desktop", viewport: { width: 1440, height: 900 } },
  { name: "mobile", viewport: { width: 390, height: 844 } },
] as const;

for (const tier of TIERS) {
  test.describe(`visual · ${tier.name}`, () => {
    test.use({ viewport: tier.viewport, colorScheme: "dark" });

    test(`login (${tier.name})`, async ({ page }) => {
      await page.goto("/");
      await page.getByText("Data Pilot").first().waitFor();
      await expect(page).toHaveScreenshot(`login-${tier.name}.png`, {
        maxDiffPixelRatio: 0.02,
        animations: "disabled",
      });
    });

    test(`chat hero (${tier.name})`, async ({ page }) => {
      await login(page, "Admin");
      await expect(page).toHaveScreenshot(`hero-${tier.name}.png`, {
        maxDiffPixelRatio: 0.02,
        animations: "disabled",
        // Greeting changes with the clock; the sidebar with usage.
        mask: [page.locator(".hero h1"), page.locator(".conv-panel")],
      });
    });

    test(`settings (${tier.name})`, async ({ page }) => {
      await login(page, "Admin");
      await page.getByRole("tab", { name: "Settings" }).click();
      await page.waitForTimeout(800);
      await expect(page).toHaveScreenshot(`settings-${tier.name}.png`, {
        maxDiffPixelRatio: 0.02,
        animations: "disabled",
        // Remembered preferences and grants vary with agent usage.
        mask: [page.locator("table")],
      });
    });
  });
}

test.describe("visual · sql (desktop)", () => {
  test.use({ viewport: { width: 1440, height: 900 }, colorScheme: "dark" });

  test("sql editor", async ({ page }) => {
    await login(page, "Admin");
    await page.getByRole("tab", { name: /SQL/ }).click();
    await page.locator(".cm-editor").waitFor({ timeout: 20000 });
    await page.waitForTimeout(600);
    await expect(page).toHaveScreenshot("sql-desktop.png", {
      maxDiffPixelRatio: 0.02,
      animations: "disabled",
      mask: [page.locator(".history-panel")],
    });
  });
});
