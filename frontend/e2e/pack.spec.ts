// Pack Inspector (s48 §P2) against the running stack: the Admin → Pack
// sub-tab renders the synced template pack (packs/nsw-property/pack.json's
// six seed layouts — L1 Cover, L2 Headline + Trend, L3 KPI + Trend,
// L4 Ranked Bars, L6 Table, L9 Sources & SQL) and the three Google links.
import { expect, test } from "@playwright/test";
import { login } from "./helpers";

test("admin sees the Pack tab with all six layouts and the Google links", async ({ page }) => {
  await login(page, "Admin");

  await page.getByRole("tab", { name: "Admin" }).click();
  await page.getByRole("button", { name: "Pack", exact: true }).click();

  await expect(page.getByTestId("pack-layout")).toHaveCount(6);

  await expect(page.getByRole("link", { name: "Open Slides pack" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Open Sheet pack" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Drive folder" })).toBeVisible();
});
