import { describe, expect, it } from "vitest";
import ar from "./ar";
import en from "./en";

function keys(value: unknown, prefix = ""): string[] {
  if (value === null || typeof value !== "object") return [prefix];
  if (Array.isArray(value)) return [prefix];
  return Object.entries(value as Record<string, unknown>).flatMap(([key, child]) =>
    keys(child, prefix ? `${prefix}.${key}` : key),
  );
}

describe("translation catalogs", () => {
  const arKeys = new Set(keys(ar));
  const enKeys = new Set(keys(en));

  it("cover exactly the same keys in Arabic and English", () => {
    expect([...enKeys].filter((key) => !arKeys.has(key))).toEqual([]);
    expect([...arKeys].filter((key) => !enKeys.has(key))).toEqual([]);
  });

  it("never ship an empty string", () => {
    const empties: string[] = [];
    for (const [lang, catalog] of [["ar", ar], ["en", en]] as const) {
      const walk = (value: unknown, prefix = "") => {
        if (typeof value === "string") {
          if (!value.trim()) empties.push(`${lang}:${prefix}`);
          return;
        }
        if (value && typeof value === "object") {
          Object.entries(value as Record<string, unknown>).forEach(([key, child]) => walk(child, prefix ? `${prefix}.${key}` : key));
        }
      };
      walk(catalog);
    }
    expect(empties).toEqual([]);
  });

  it("translate the product name identically", () => {
    expect(ar.app.name).toBe(en.app.name);
  });
});
