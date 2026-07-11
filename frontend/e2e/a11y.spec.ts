// s13 Phase F: axe scans on the primary surfaces — fails on serious/critical
// violations (contrast, names, roles). Moderate/minor findings are reported
// in the failure payload but don't gate.
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { login } from "./helpers";

// Scan the brand-default dark theme; light gets the same tokens re-audited
// through the visual suite when its baselines change.
test.use({ colorScheme: "dark" });

async function expectNoSeriousViolations(page: import("@playwright/test").Page) {
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
  await page.getByText("Datapilot").waitFor();
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
