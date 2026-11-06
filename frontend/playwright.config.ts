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

// Browser binaries: by default Playwright uses its own downloaded Chromium
// (`npx playwright install chromium`). Where the Playwright CDN is unreachable, the
// `@sparticuz/chromium` npm package ships a self-contained Chromium + al2023 shared
// libraries through the npm registry; `npm run e2e:local` prepares it and launches with it.
// See docs/E2E.md §2 and `e2e/browser.mjs`.
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;
const launchOptions = executablePath
  ? { executablePath, args: (process.env.PLAYWRIGHT_CHROMIUM_ARGS ?? "").split(" ").filter(Boolean) }
  : {};

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
    // `video` shells out to Playwright's ffmpeg, which ships from the same CDN as the browsers.
    // In CDN-less mode (@sparticuz/chromium) there is no ffmpeg, so video stays off there;
    // trace and screenshot carry the failure evidence.
    video: executablePath ? "off" : "retain-on-failure",
    locale: "ar-EG",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], launchOptions } }],
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
        command: "npm run dev -- --port 5173",
        url: baseURL,
        reuseExistingServer: true,
        timeout: 120_000,
      },
});
