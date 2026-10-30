import { describe, expect, it } from "vitest";
import { compact, date, daysUntil, initials, money, num, percent, relative } from "./format";

describe("money", () => {
  it("formats EGP for both locales with the currency code attached", () => {
    expect(money("125000.5", "EGP", "en")).toContain("125,000.5");
    expect(money("125000.5", "EGP", "ar")).toContain("125");
  });

  it("renders an em dash for missing or non-numeric values", () => {
    expect(money(null)).toBe("—");
    expect(money(undefined)).toBe("—");
    expect(money("not-a-number")).toBe("—");
  });
});

describe("num", () => {
  it("uses Latin digits in Arabic (procurement teams read Western numerals)", () => {
    expect(num(1234.567, "ar", 2)).toBe("1,234.57");
    expect(num(1234.567, "en", 1)).toBe("1,234.6");
  });

  it("handles invalid input", () => {
    expect(num("abc")).toBe("—");
  });
});

describe("percent / compact", () => {
  it("formats percentages with a single decimal by default", () => {
    expect(percent(12.345)).toBe("12.3%");
    expect(percent(null)).toBe("—");
  });

  it("compacts large numbers for chart axes", () => {
    expect(compact(1_500_000, "en")).toBe("1.5M");
    expect(compact(9_400, "en")).toBe("9.4K");
  });
});

describe("dates", () => {
  it("formats ISO timestamps and tolerates null", () => {
    expect(date("2026-03-04T10:00:00Z", "en")).toMatch(/2026/);
    expect(date(null)).toBe("—");
  });

  it("reports days remaining including negative values", () => {
    const future = new Date(Date.now() + 3 * 86_400_000).toISOString();
    expect(daysUntil(future)).toBeGreaterThanOrEqual(2);
    expect(daysUntil(null)).toBeNull();
  });

  it("describes relative deadlines", () => {
    const soon = new Date(Date.now() + 2 * 86_400_000).toISOString();
    expect(relative(soon, "en")).toMatch(/in|2/);
    expect(relative(null, "en")).toBe("—");
  });
});

describe("initials", () => {
  it("takes up to two initials from a display name", () => {
    expect(initials("Nour El Din")).toBe("NE");
    expect(initials("Mona")).toBe("M");
    expect(initials("")).toBe("?");
  });
});
