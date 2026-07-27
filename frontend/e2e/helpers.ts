// Shared E2E helpers: dev-auth login + navigation to Template Studio.
import { Locator, Page, expect } from "@playwright/test";

/** Pick a value from a KitSelect (s33 · Radix Select replaced every native
 *  <select>). Radix portals its listbox to the body, so the option click is
 *  page-scoped even when the trigger sits inside a card. KitSelect stamps
 *  data-value on the trigger AND on every option, so specs still address the
 *  same values the old `selectOption()` calls used — labels stay free to
 *  change without touching a test. */
export async function pickOption(page: Page, trigger: Locator, value: string) {
  await trigger.click();
  await page.locator(`[role="option"][data-value="${value}"]`).click();
  await expect(trigger).toHaveAttribute("data-value", value);
}

/** Sign in through the dev-auth stub exactly like a human: click the user card. */
export async function login(page: Page, user: "Admin" | "User One" | "User Two" = "Admin") {
  await page.goto("/");
  await page.getByText(user, { exact: true }).click();
  // Landed in the app shell (Chat tab is the default route).
  await expect(page.getByPlaceholder(/Ask about/)).toBeVisible();
}

/** Open Admin → Template Studio. */
export async function openTemplateStudio(page: Page) {
  await page.getByRole("tab", { name: "Admin" }).click();
  await page.getByRole("button", { name: "Template Studio" }).click();
  await expect(page.getByTestId("template-preview")).toBeVisible();
}
