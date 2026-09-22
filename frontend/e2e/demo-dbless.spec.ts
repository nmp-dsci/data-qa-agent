// The DB-less demo (s52) through a visitor's eyes, against a backend started
// with `make demo-dbless-up` (DEMO_MODE=1 DB_DISABLED=1 — no Postgres at all)
// and the exhibit dump in frontend/public/exhibits/ (`make export-exhibits`).
//
// Self-skips unless DEMO_DBLESS=1: every other spec assumes the dev backend,
// and this one asserts the opposite contract (no Explore/SQL, static exhibits,
// no API call outside the demo surface). Run it with:
//
//   make demo-dbless-up && cd frontend && DEMO_DBLESS=1 npx playwright test demo-dbless
import { expect, Page, test } from "@playwright/test";

test.skip(!process.env.DEMO_DBLESS, "set DEMO_DBLESS=1 against a `make demo-dbless-up` stack");

const API = process.env.E2E_API_URL ?? `http://localhost:${process.env.API_HOST_PORT ?? "8010"}`;
// The only API paths a DB-less demo session may touch.
const ALLOWED_API = /\/(auth\/|ask|events|health|demo\/|conversations|me$)/;

async function enterDemo(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: /Enter demo/ }).click();
  await expect(page.getByPlaceholder(/Ask about/)).toBeVisible();
}

test("door → chat → every exhibit tab, from the static dump, with no stray API calls", async ({
  page,
}) => {
  const consoleErrors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() !== "error") return;
    const url = msg.location().url;
    // Two resource-load lines are the demo working as designed, not defects:
    // the cookie-less first visit's session probe (GET /me -> 401, which
    // resumeSession swallows), and the per-IP events rate limit (POST /events
    // -> 429) that back-to-back runs of this spec trip on the same minute.
    if (/\/me$/.test(url) && /401/.test(msg.text())) return;
    if (/\/events$/.test(url) && /429/.test(msg.text())) return;
    consoleErrors.push(`${msg.text()} @ ${url}`);
  });
  page.on("pageerror", (err) => consoleErrors.push(`pageerror: ${err.message}`));
  const apiHits: string[] = [];
  const exhibitHits: string[] = [];
  page.on("request", (req) => {
    const url = req.url();
    if (url.startsWith(API)) apiHits.push(url.slice(API.length));
    if (url.includes("/exhibits/")) exhibitHits.push(url);
  });

  await enterDemo(page);
  // The landing card wakes no database, so the login never narrates a wait.
  await expect(page.getByText(/Waking warehouse/)).toHaveCount(0);

  // Dev-only surfaces are gone from the rail.
  await expect(page.getByRole("tab", { name: "Chat" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Explore" })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "SQL Editor" })).toHaveCount(0);
  // …and from the command palette.
  await page.keyboard.press("Meta+K");
  await expect(page.getByText("Go to Chat")).toBeVisible();
  await expect(page.getByText("Go to Explore")).toHaveCount(0);
  await expect(page.getByText("Go to SQL Editor")).toHaveCount(0);
  // Escape is handled on the palette's input; ⌘K toggles from anywhere.
  await page.keyboard.press("Meta+K");
  await expect(page.getByRole("dialog", { name: "Command palette" })).toHaveCount(0);

  // A chip question replays a recorded answer.
  const chip = page.getByText("Which suburbs had the fastest rent growth last year?").first();
  await chip.click();
  await expect(page.getByText("demo_replay", { exact: false })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("button", { name: "show SQL" })).toBeVisible();
  await expect(page.getByRole("button", { name: "open in SQL editor" })).toHaveCount(0);

  // Goldens: list + detail from the dump; the warehouse-backed controls are inert.
  await page.getByRole("tab", { name: "Golden Examples" }).click();
  const goldens = page.getByRole("main", { name: "Golden Examples" });
  const firstGolden = goldens.locator('[data-testid^="golden-row-"]').first();
  await expect(firstGolden).toBeVisible();
  await firstGolden.click();
  await expect(goldens.getByTestId("golden-sql")).not.toHaveValue("");
  await expect(goldens.getByTestId("golden-run-sql")).toBeDisabled();
  await expect(goldens.getByTestId("golden-save")).toBeDisabled();
  await expect(goldens.getByText("Not available — demo only").first()).toBeVisible();

  // Evaluations: the run list, then a second run's detail drill-down.
  await page.getByRole("tab", { name: "Evaluations" }).click();
  await expect(page.getByText("runs", { exact: true })).toBeVisible();
  await expect(page.getByText("overall pass").first()).toBeVisible();
  const runCards = page.locator("aside button");
  expect(await runCards.count()).toBeGreaterThan(1);
  const detailHitsBefore = exhibitHits.filter((u) => u.includes("/eval-runs/")).length;
  await runCards.nth(1).click();
  await expect
    .poll(() => exhibitHits.filter((u) => u.includes("/eval-runs/")).length)
    .toBeGreaterThan(detailHitsBefore);
  await expect(page.getByText("overall pass").first()).toBeVisible();

  // Operations: the deck renders from the snapshot and windows switch.
  await page.getByRole("tab", { name: "Operations" }).click();
  const deck = page.getByRole("main", { name: "Operations" });
  await expect(deck.getByText("data pilot · ops")).toBeVisible();
  await expect(deck.getByText(/rollup refreshed/)).toBeVisible();
  await deck.getByRole("button", { name: "7d" }).click();
  await expect(deck.getByText(/telemetry · 7d/)).toBeVisible();
  await expect(deck.getByRole("button", { name: "refresh" })).toBeDisabled();

  // Architecture: system map + knowledge base content + a real run's trace.
  await page.getByRole("tab", { name: "Architecture" }).click();
  const arch = page.getByRole("main", { name: "Architecture" });
  await expect(arch.getByText("data pilot · architecture")).toBeVisible();
  await expect(arch.getByText(/knowledge_version/)).toBeVisible();
  await expect(arch.locator(".arch-kb-content")).toContainText("data-insight agent");
  await arch.getByRole("button", { name: /domains\/property-rent\/bedrooms/ }).click();
  await expect(arch.locator(".arch-kb-edit-textarea")).not.toHaveValue("");

  // Admin: the observability band has numbers, not an error line.
  await page.getByRole("tab", { name: "Admin" }).click();
  const admin = page.locator("main.admin");
  await expect(admin.getByText("Users", { exact: true })).toBeVisible();
  await expect(admin.getByText("Query Runs", { exact: true })).toBeVisible();
  await expect(admin.locator("p.error")).toHaveCount(0);

  // A hand-typed dev-only route falls back to Chat, same as a blocked admin route.
  await page.goto("/explore");
  await expect(page.getByPlaceholder(/Ask about/)).toBeVisible();
  await expect(page).toHaveURL(/\/chat$/);
  await page.goto("/sql");
  await expect(page).toHaveURL(/\/chat$/);
  await page.goto("/analytics");
  await expect(page).toHaveURL(/\/chat$/);

  // The exhibits came from the SPA origin; the API saw only the demo surface.
  expect(exhibitHits.length).toBeGreaterThan(10);
  const stray = apiHits.filter((p) => !ALLOWED_API.test(p));
  expect(stray, `API paths outside the demo surface: ${stray.join(", ")}`).toEqual([]);
  expect(consoleErrors, consoleErrors.join("\n")).toEqual([]);
});
