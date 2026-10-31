import { expect, test } from "@playwright/test";

import { DEMO, login, trackConsoleErrors, useLanguage } from "./support/auth";

/**
 * Sign-in, sign-out and the Arabic-first interface.
 *
 * Arabic is the default language and the layout is RTL; English is a switch, not a second design.
 */
test.describe("authentication", () => {
  test("the sign-in screen is usable and RTL by default", async ({ page }) => {
    await page.goto("/login");
    await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
    await expect(page.locator("#email")).toBeVisible();
    await expect(page.locator("#password")).toBeVisible();
  });

  test("signing in lands on the dashboard", async ({ page }) => {
    const errors = trackConsoleErrors(page);
    await login(page, DEMO.manager);
    await expect(page.locator("main, [role='main']").first()).toBeVisible();
    expect(errors, "the console must stay clean while signing in").toEqual([]);
  });

  test("a wrong password is refused with a readable message", async ({ page }) => {
    await page.goto("/login");
    await page.locator("#email").fill(DEMO.manager);
    await page.locator("#password").fill("not-the-password");
    await page.locator('form button[type="submit"]').click();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page).toHaveURL(/\/login/);
  });

  test("an anonymous visitor is sent to the sign-in screen", async ({ page }) => {
    await page.goto("/purchase-orders");
    await expect(page).toHaveURL(/\/login/);
  });

  test("signing out ends the session", async ({ page }) => {
    await login(page, DEMO.buyer);
    await page
      .getByRole("button", { name: /تسجيل الخروج|sign out|خروج/i })
      .first()
      .click();
    await expect(page).toHaveURL(/\/login/);
    await page.goto("/");
    await expect(page).toHaveURL(/\/login/);
  });

  test("the interface switches to English and back to Arabic", async ({ page }) => {
    await login(page, DEMO.buyer);
    await useLanguage(page, "en");
    await expect(page.locator("html")).toHaveAttribute("dir", "ltr");
    await useLanguage(page, "ar");
    await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  });
});
