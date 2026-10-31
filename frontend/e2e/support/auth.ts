import { expect, type Page } from "@playwright/test";

/** Demo accounts seeded by `procureai bootstrap --demo` (see docs/RUNBOOK.md). */
export const PASSWORD = "Demo!Pilot2026";

export const DEMO = {
  admin: "admin@pilot.procureai",
  manager: "manager@nile.example.com",
  buyer: "buyer@nile.example.com",
  requester: "requester@nile.example.com",
  finance: "finance@nile.example.com",
  storekeeper: "storekeeper@nile.example.com",
  controller: "controller@nile.example.com",
  auditor: "auditor@nile.example.com",
} as const;

/** Sign in through the real form (no token injection: the login screen is part of what we test). */
export async function login(page: Page, email: string, password: string = PASSWORD): Promise<void> {
  await page.goto("/login");
  await page.locator("#email").fill(email);
  await page.locator("#password").fill(password);
  await page.locator('form button[type="submit"]').click();
  await expect(page).not.toHaveURL(/\/login/);
  await expect(page.locator("html")).toHaveAttribute("lang", /ar|en/);
}

/** Switch the interface language the way the shell does, then reload. */
export async function useLanguage(page: Page, lang: "ar" | "en"): Promise<void> {
  await page.addInitScript((value) => {
    window.localStorage.setItem("procureai.lang", value as string);
  }, lang);
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("dir", lang === "ar" ? "rtl" : "ltr");
}

/** Collect the browser console errors that must not happen on a healthy screen. */
export function trackConsoleErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  return errors;
}
