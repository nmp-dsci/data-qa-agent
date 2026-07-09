// Playwright E2E — drives the running local stack (make up) through the same
// interface a human uses: the browser at localhost:5230. No mocks: the suite
// logs in via dev-auth, walks Template Studio + the playground, and (in
// chat.spec.ts) asks the live agent a real question.
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  // One worker: the specs share the backend + a single dev-auth session model.
  workers: 1,
  fullyParallel: false,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5230",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
