// Shared E2E helpers: dev-auth login + navigation to Template Studio.
import { Page, expect } from "@playwright/test";

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
