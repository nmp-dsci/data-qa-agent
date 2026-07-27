// The Ops flight deck (s32 W0) against the running stack.
//
// Two things worth a browser: the tab is genuinely admin-gated (a non-admin
// must not even see it, since the deck exposes cross-user operational data),
// and the deck renders its frame on a COLD rollup — the first ever load has no
// ops_rollup row at all, and it must show the annunciator band and the readouts
// with em-dashes rather than blocking, erroring, or white-screening.
import { expect, test } from "@playwright/test";
import { login } from "./helpers";

test("admin sees the Ops tab and the deck renders on a cold rollup", async ({ page }) => {
  await login(page, "Admin");

  await page.getByRole("tab", { name: "Ops" }).click();

  // The frame, not the numbers: a cold rollup has no data yet, and that is the
  // state this assertion is protecting.
  const deck = page.getByRole("main", { name: "Operations" });
  await expect(deck).toBeVisible();
  await expect(deck.getByText("data pilot · ops")).toBeVisible();

  // The annunciator band always renders — the guarantees lamp is unconditional.
  await expect(deck.getByText("rls · audit")).toBeVisible();
  // …and the SLO lamps render whether or not they have data behind them.
  await expect(deck.getByText(/^availability /)).toBeVisible();
  await expect(deck.getByText(/^ttfp p95 /)).toBeVisible();

  // The Tier-1 readouts exist even with an empty window.
  await expect(deck.getByText("full answer p95")).toBeVisible();
  await expect(deck.getByText("time to first page p95")).toBeVisible();
  await expect(deck.getByText("cost / answer")).toBeVisible();

  // Window switching is the only stateful control on the page.
  await deck.getByRole("button", { name: "7d" }).click();
  await expect(deck.getByRole("button", { name: "7d" })).toHaveAttribute("aria-pressed", "true");
  await expect(deck.getByText(/telemetry · 7d/)).toBeVisible();
});

test("a non-admin has no Ops tab and cannot deep-link to it", async ({ page }) => {
  await login(page, "User One");

  await expect(page.getByRole("tab", { name: "Ops" })).toHaveCount(0);

  // The route guard bounces a hand-typed /ops back to chat rather than
  // rendering an empty deck that then 403s on every fetch.
  await page.goto("/ops");
  await expect(page.getByPlaceholder(/Ask about/)).toBeVisible();
  await expect(page).toHaveURL(/\/chat$/);
});

test("refresh builds the rollup and stamps it", async ({ page }) => {
  await login(page, "Admin");
  await page.getByRole("tab", { name: "Ops" }).click();

  const deck = page.getByRole("main", { name: "Operations" });
  await deck.getByRole("button", { name: "refresh" }).click();

  // The refresh is synchronous by design (the admin asked to wait for numbers),
  // so once it settles the header must report a real rollup age instead of the
  // "not built yet" copy.
  await expect(deck.getByText(/rollup refreshed .* ago/)).toBeVisible({
    timeout: 30_000,
  });
});
