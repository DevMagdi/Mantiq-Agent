import { defineConfig, devices } from "@playwright/test";

/**
 * Browser end-to-end configuration.
 *
 * Start the stack first (or let `webServer` do it):
 *   backend:  uvicorn app.main:app --port 8000   (seeded demo tenant)
 *   frontend: npm run dev -- --port 5173         (proxies /api and /ws to :8000)
 *
 * Then: `npx playwright install chromium && npm run e2e`.
 * `E2E_BASE_URL` points the suite at a deployed environment instead (it must have the demo tenant).
 */
const baseURL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173";

export default defineConfig({
  testDir: "./e2e",
  // The suite signs in to the shared demo tenant and mutates it, so it runs one worker at a time.
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    locale: "ar-EG",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
        command: "npm run dev -- --port 5173",
        url: baseURL,
        reuseExistingServer: true,
        timeout: 120_000,
      },
});
