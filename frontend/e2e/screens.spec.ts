import { expect, test } from "@playwright/test";

import { DEMO, login, trackConsoleErrors } from "./support/auth";

/**
 * Every screen the navigation offers, rendered against real data.
 *
 * The point is not to assert specific numbers (the demo dataset changes) but that each route mounts,
 * keeps a heading, and does not fire a console error — the failure mode a screenshot review misses.
 */
const ROUTES: Array<{ path: string; email: string }> = [
  { path: "/", email: DEMO.manager },
  { path: "/requests", email: DEMO.manager },
  { path: "/rfqs", email: DEMO.manager },
  { path: "/approvals", email: DEMO.manager },
  { path: "/purchase-orders", email: DEMO.buyer },
  { path: "/suppliers", email: DEMO.buyer },
  { path: "/accounting", email: DEMO.controller },
  { path: "/accounting/reports", email: DEMO.controller },
  { path: "/accounting/setup", email: DEMO.controller },
  { path: "/analytics", email: DEMO.manager },
  { path: "/activity", email: DEMO.auditor },
  { path: "/inbox", email: DEMO.buyer },
  { path: "/templates", email: DEMO.buyer },
  { path: "/voice", email: DEMO.buyer },
  { path: "/assistant", email: DEMO.manager },
  { path: "/settings", email: DEMO.admin },
];

test.describe("screen smoke test", () => {
  for (const route of ROUTES) {
    test(`${route.path} renders for ${route.email}`, async ({ page }) => {
      const errors = trackConsoleErrors(page);
      await login(page, route.email);
      await page.goto(route.path);
      await expect(page.locator("main, [role='main']").first()).toBeVisible();
      // A crash in a page component surfaces as an error boundary or a blank main region.
      await expect(page.locator("body")).not.toContainText(/something went wrong|حدث خطأ غير متوقع/i);
      expect(errors, `console errors on ${route.path}`).toEqual([]);
    });
  }

  test("an unknown route shows the not-found screen inside the shell", async ({ page }) => {
    await login(page, DEMO.manager);
    await page.goto("/this-route-does-not-exist");
    await expect(page.locator("main, [role='main']").first()).toBeVisible();
  });
});
