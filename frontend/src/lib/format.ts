/** Locale-aware formatting. Arabic UI still uses Western digits by default for procurement clarity. */
export type Lang = "ar" | "en";

const CURRENCY_DECIMALS: Record<string, number> = { KWD: 3, BHD: 3, OMR: 3, JOD: 3, EGP: 2, USD: 2, EUR: 2 };

export function money(value: string | number | null | undefined, currency = "EGP", lang: Lang = "en"): string {
  if (value === null || value === undefined || value === "") return "—";
  const amount = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(amount)) return "—";
  const decimals = CURRENCY_DECIMALS[currency.toUpperCase()] ?? 2;
  const formatted = new Intl.NumberFormat(lang === "ar" ? "ar-EG-u-nu-latn" : "en-GB", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(amount);
  return lang === "ar" ? `${formatted} ${currency}` : `${currency} ${formatted}`;
}

export function compact(value: number | null | undefined, lang: Lang = "en"): string {
  if (value == null || !Number.isFinite(value)) return "—";
  // `Intl` lower-cases compact suffixes in some ICU builds; dashboards expect "1.5M".
  return new Intl.NumberFormat(lang === "ar" ? "ar-EG-u-nu-latn" : "en-GB", {
    notation: "compact",
    maximumFractionDigits: 1,
  })
    .format(value)
    .toUpperCase();
}

export function num(value: number | string | null | undefined, lang: Lang = "en", digits = 0): string {
  const amount = typeof value === "string" ? Number(value) : value;
  if (amount == null || !Number.isFinite(amount)) return "—";
  return new Intl.NumberFormat(lang === "ar" ? "ar-EG-u-nu-latn" : "en-GB", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(amount);
}

export function percent(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${value.toFixed(digits)}%`;
}

export function date(value: string | null | undefined, lang: Lang = "en"): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return new Intl.DateTimeFormat(lang === "ar" ? "ar-EG-u-nu-latn" : "en-GB", {
    year: "numeric",
    month: "short",
    day: "2-digit",
  }).format(parsed);
}

export function dateTime(value: string | null | undefined, lang: Lang = "en"): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return new Intl.DateTimeFormat(lang === "ar" ? "ar-EG-u-nu-latn" : "en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(parsed);
}

export function relative(value: string | null | undefined, lang: Lang = "en"): string {
  if (!value) return "—";
  const parsed = new Date(value).getTime();
  if (Number.isNaN(parsed)) return "—";
  const diffSeconds = Math.round((parsed - Date.now()) / 1000);
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ["day", 86_400],
    ["hour", 3_600],
    ["minute", 60],
  ];
  const rtf = new Intl.RelativeTimeFormat(lang === "ar" ? "ar-EG" : "en-GB", { numeric: "auto" });
  for (const [unit, seconds] of units) {
    if (Math.abs(diffSeconds) >= seconds) return rtf.format(Math.round(diffSeconds / seconds), unit);
  }
  return rtf.format(diffSeconds, "second");
}

export function initials(name: string): string {
  const result = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
  return result || "?";
}

export function daysUntil(value: string | null | undefined): number | null {
  if (!value) return null;
  const target = new Date(value).getTime();
  if (Number.isNaN(target)) return null;
  return Math.ceil((target - Date.now()) / 86_400_000);
}
