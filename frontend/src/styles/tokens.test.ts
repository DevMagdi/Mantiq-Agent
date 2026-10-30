import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * The design contract, enforced rather than documented: one navy theme, exact brand hex values,
 * and every ink/surface pair used for text clearing WCAG AA. If someone reintroduces a light theme
 * through a media query, or tweaks a colour until it fails contrast, this test fails first.
 */
// Vitest runs with the frontend package as its root, so these paths are stable.
const read = (path: string) => readFileSync(resolve(process.cwd(), path), "utf8");
const css = read("src/styles/tokens.css");
const tailwind = read("tailwind.config.ts");

function token(name: string): string {
  const match = new RegExp(`--${name}:\\s*([^;]+);`).exec(css);
  if (!match) throw new Error(`missing token --${name}`);
  return match[1]!.trim();
}

function luminance(hex: string): number {
  const value = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((offset) => {
    const channel = Number.parseInt(value.slice(offset, offset + 2), 16) / 255;
    return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(foreground: string, background: string): number {
  const [a, b] = [luminance(foreground), luminance(background)].sort((x, y) => y - x) as [number, number];
  return (a + 0.05) / (b + 0.05);
}

describe("design tokens", () => {
  it("ship exactly one theme", () => {
    expect(css).toContain("color-scheme: dark");
    expect(css).not.toMatch(/prefers-color-scheme/);
    expect(css).not.toMatch(/\[data-theme/);
  });

  it("keep the brand values the SRS fixes", () => {
    expect(token("navy")).toBe("#16283f");
    expect(token("gold")).toBe("#c9a227");
    expect(tailwind).toContain("Inter");
    expect(tailwind).toContain("IBM Plex Sans Arabic");
    expect(css).toContain("tabular-nums");
  });

  it("clear WCAG AA for every text pair on every surface", () => {
    const surfaces = ["surface", "surface-alt", "surface-raised"].map(token);
    const inks = ["ink", "ink-muted", "ink-subtle", "gold", "gold-300", "ok-ink", "warn-ink", "danger-ink", "info-ink"].map(token);
    for (const ink of inks) {
      for (const surface of surfaces) {
        expect(contrast(ink, surface), `${ink} on ${surface}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });

  it("clear AA in the sidebar, which is darker than the page", () => {
    const rail = token("sidebar");
    for (const name of ["sidebar-ink", "sidebar-ink-muted", "sidebar-label"]) {
      expect(contrast(token(name), rail), name).toBeGreaterThanOrEqual(4.5);
    }
    // The active label sits on the raised rail wash, not on the rail base.
    expect(contrast(token("gold-200"), token("sidebar-raised"))).toBeGreaterThanOrEqual(4.5);
    // Primary buttons: dark navy text on gold.
    expect(contrast(token("gold-contrast"), token("gold"))).toBeGreaterThanOrEqual(4.5);
  });

  it("honour reduced motion", () => {
    expect(css).toContain("prefers-reduced-motion: reduce");
  });
});
