// The Architecture tab (M5, agent_sdk migration) against the running stack.
//
// Mirrors ops.spec.ts: the tab is admin-gated (the underlying data is a live
// system snapshot + real run traces), and the four panels — system map,
// knowledge base, tools registry, run walk-through — must render from the
// live data-agent rather than white-screening.
import { expect, test } from "@playwright/test";
import { login } from "./helpers";

test("admin sees the Architecture tab and all four panels render live data", async ({ page }) => {
  await login(page, "Admin");

  await page.getByRole("tab", { name: "Architecture" }).click();

  const tab = page.getByRole("main", { name: "Architecture" });
  await expect(tab).toBeVisible();
  await expect(tab.getByText("data pilot · architecture")).toBeVisible();

  // System map — the live runtime/model badge, not a placeholder.
  await expect(tab.getByRole("strong").filter({ hasText: "Agent runtime" })).toBeVisible();
  await expect(tab.getByText(/^av-/)).toBeVisible();

  // Knowledge base — the workflow template is always the first list item, and
  // its content loads into the detail pane without a click.
  await expect(tab.getByText("knowledge_version")).toBeVisible();
  await expect(tab.getByRole("button", { name: /CLAUDE\.md/ })).toBeVisible();
  await expect(tab.getByText("Rendered per-run as the agent_sdk runtime's system prompt.")).toBeVisible();

  // Tools & MCP registry — the governed extract tool is always present.
  await expect(tab.getByText("mcp__dp__extract")).toBeVisible();
  await expect(tab.getByText("sql_guardrails + RLS")).toBeVisible();

  // Run walk-through — at least one prior agent run exists on this stack
  // (chat.spec.ts / the manual smoke above have both asked real questions),
  // and picking it renders the trace stepper.
  await expect(tab.getByText("Pick a run on the left.").or(tab.locator(".agent-trace"))).toBeVisible();
});

test("a non-admin has no Architecture tab and cannot deep-link to it", async ({ page }) => {
  await login(page, "User One");

  await expect(page.getByRole("tab", { name: "Architecture" })).toHaveCount(0);

  await page.goto("/architecture");
  await expect(page.getByPlaceholder(/Ask about/)).toBeVisible();
  await expect(page).toHaveURL(/\/chat$/);
});
