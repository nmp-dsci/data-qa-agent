// s13 Phase F: axe scans on the primary surfaces — fails on serious/critical
// violations (contrast, names, roles). Moderate/minor findings are reported
// in the failure payload but don't gate.
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { login } from "./helpers";

// Scan the brand-default dark theme; light gets the same tokens re-audited
// through the visual suite when its baselines change.
//
// reducedMotion is load-bearing, not a preference: axe computes contrast from
// composited colour, so an element caught mid-fade is measured at partial
// opacity and reports a violation the user never sees. The s33 login opens
// with a staggered entrance, which made this flake — the gold avatar blended
// to #a58b58 against the card. Reduced motion collapses every duration to ~0,
// so all three scans assess the resting state, which is what an audit should
// judge (and is itself the accessibility-relevant configuration).
test.use({ colorScheme: "dark", reducedMotion: "reduce" });

/** Wait for every finite animation to finish before scanning. Infinite ones
 *  (the login's HUD heading tape) are skipped — they never finish, and their
 *  elements carry no contrast-critical text. */
async function settle(page: import("@playwright/test").Page) {
  await page.waitForFunction(() =>
    document.getAnimations().every((a) => {
      const timing = a.effect?.getComputedTiming();
      return timing?.iterations === Infinity || a.playState === "finished";
    }),
  );
}

async function expectNoSeriousViolations(page: import("@playwright/test").Page) {
  await settle(page);
  const results = await new AxeBuilder({ page }).analyze();
  const gating = results.violations.filter((v) =>
    ["serious", "critical"].includes(v.impact ?? ""),
  );
  expect(
    gating,
    gating
      .map((v) => `${v.impact}: ${v.id} — ${v.nodes.map((n) => n.target).join(", ")}`)
      .join("\n"),
  ).toEqual([]);
}

test("login is axe-clean", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Data Pilot").first().waitFor();
  await expectNoSeriousViolations(page);
});

test("chat hero is axe-clean", async ({ page }) => {
  await login(page, "Admin");
  await expectNoSeriousViolations(page);
});

test("settings is axe-clean", async ({ page }) => {
  await login(page, "Admin");
  await page.getByRole("tab", { name: "Settings" }).click();
  await page.waitForTimeout(600);
  await expectNoSeriousViolations(page);
});
