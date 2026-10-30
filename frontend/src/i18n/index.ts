import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import ar from "./ar";
import en from "./en";

export const SUPPORTED_LANGS = ["ar", "en"] as const;
export type AppLang = (typeof SUPPORTED_LANGS)[number];

const stored = (typeof localStorage !== "undefined" ? localStorage.getItem("procureai.lang") : null) as AppLang | null;
/** Arabic (RTL) is the default language of the product. */
export const defaultLang: AppLang = stored ?? "ar";

void i18n.use(initReactI18next).init({
  resources: { ar: { translation: ar }, en: { translation: en } },
  lng: defaultLang,
  fallbackLng: "ar",
  interpolation: { escapeValue: false },
  returnNull: false,
});

export function applyDocumentLang(lang: AppLang): void {
  document.documentElement.lang = lang;
  document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
  localStorage.setItem("procureai.lang", lang);
}

applyDocumentLang(defaultLang);

export default i18n;
