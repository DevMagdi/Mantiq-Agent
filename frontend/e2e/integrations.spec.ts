import { expect, test, type Page } from "@playwright/test";

import { DEMO, login, trackConsoleErrors } from "./support/auth";

/**
 * The Integrations screen, as an operator uses it.
 *
 * These specs never invent credentials: the "save" cases use a deliberately incomplete
 * configuration, because the assertion that matters is that the screen reports the truth
 * (`Error` + the missing field names) instead of a green badge. A run against a stack that has real
 * vendor credentials can extend the same helpers to assert `Live` after a successful probe.
 */
async function openIntegrations(page: Page): Promise<void> {
  await page.goto("/settings");
  await page.getByRole("tab", { name: /Integrations|التكاملات/ }).click();
  await expect(page.getByText(/Language model|نموذج اللغة/).first()).toBeVisible();
}

async function apiToken(page: Page): Promise<string> {
  const token = await page.evaluate(() => sessionStorage.getItem("procureai.access"));
  expect(token, "the access token should be in sessionStorage after login").toBeTruthy();
  return token as string;
}

test.describe("integrations", () => {
  test("lists every provider with real fields instead of an empty JSON box", async ({ page }) => {
    const errors = trackConsoleErrors(page);
    await login(page, DEMO.admin);
    await openIntegrations(page);

    // Catalogue-driven inputs: a secret field is a password input with a label, not a textarea.
    const apiKey = page.getByLabel(/API key|مفتاح الـ API/).first();
    await expect(apiKey).toBeVisible();
    await expect(apiKey).toHaveAttribute("type", "password");
    await expect(page.getByLabel(/Model|النموذج/).first()).toBeVisible();
    await expect(page.getByText(/Config \(JSON\)/)).toHaveCount(0);

    // Every card states one of the four honest statuses.
    await expect(
      page.getByText(/^(Live|Sandbox|Not configured|Error|Disabled|يعمل فعليًا|بيئة تجريبية|غير مضبوط|خطأ|معطّل)$/).first(),
    ).toBeVisible();
    expect(errors, "console errors on the integrations screen").toEqual([]);
  });

  test("an incomplete configuration is reported as an error with its missing fields", async ({ page }) => {
    await login(page, DEMO.admin);
    await openIntegrations(page);
    const token = await apiToken(page);

    // Start from a clean slate, then save Brave without a key — through the UI.
    await page.request.delete("/api/v1/integrations/search", {
      headers: { authorization: `Bearer ${token}` },
    });
    await page.reload();
    await page.getByRole("tab", { name: /Integrations|التكاملات/ }).click();

    const card = page.locator("div").filter({ hasText: /Supplier discovery|استكشاف الموردين/ }).last();
    await card.getByLabel(/Provider|المزوّد/).selectOption("brave");
    await card.getByRole("button", { name: /^Save$|^حفظ$/ }).click();

    await expect(page.getByText(/Missing required fields|حقول مطلوبة ناقصة/).first()).toBeVisible();
    await expect(card.getByText(/api_key/).first()).toBeVisible();

    await page.request.delete("/api/v1/integrations/search", {
      headers: { authorization: `Bearer ${token}` },
    });
  });

  test("disabling a provider hands traffic back and the badge changes", async ({ page }) => {
    await login(page, DEMO.admin);
    await openIntegrations(page);
    const token = await apiToken(page);

    // Configure a complete-but-unreachable SMTP box straight through the API, so the screen has a
    // tenant-owned row to toggle.
    await page.request.put("/api/v1/integrations/email", {
      headers: { authorization: `Bearer ${token}` },
      data: {
        provider: "smtp",
        config: { host: "127.0.0.1", port: "1", from_address: "rfq@nile.example.com" },
        secrets: {},
        enabled: true,
      },
    });
    await page.reload();
    await page.getByRole("tab", { name: /Integrations|التكاملات/ }).click();

    const emailCard = page.locator("div").filter({ hasText: /^Email|البريد الإلكتروني/ }).last();
    await emailCard.getByRole("button", { name: /Disable|تعطيل/ }).click();
    await expect(emailCard.getByText(/Disabled|معطّل/)).toBeVisible();

    await emailCard.getByRole("button", { name: /Enable|تفعيل/ }).click();
    await expect(emailCard.getByText(/Error|خطأ/)).toBeVisible();

    await page.request.delete("/api/v1/integrations/email", {
      headers: { authorization: `Bearer ${token}` },
    });
  });
});
