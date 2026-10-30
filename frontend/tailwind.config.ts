import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        navy: {
          DEFAULT: "rgb(var(--navy-rgb) / <alpha-value>)",
          900: "rgb(var(--navy-900-rgb) / <alpha-value>)",
          800: "rgb(var(--navy-800-rgb) / <alpha-value>)",
          700: "rgb(var(--navy-700-rgb) / <alpha-value>)",
        },
        gold: {
          DEFAULT: "rgb(var(--gold-rgb) / <alpha-value>)",
          600: "rgb(var(--gold-600-rgb) / <alpha-value>)",
          300: "rgb(var(--gold-300-rgb) / <alpha-value>)",
        },
        surface: {
          DEFAULT: "rgb(var(--surface-rgb) / <alpha-value>)",
          alt: "rgb(var(--surface-alt-rgb) / <alpha-value>)",
          raised: "rgb(var(--surface-raised-rgb) / <alpha-value>)",
        },
        line: "rgb(var(--line-rgb) / <alpha-value>)",
        ink: {
          DEFAULT: "rgb(var(--ink-rgb) / <alpha-value>)",
          muted: "rgb(var(--ink-muted-rgb) / <alpha-value>)",
          subtle: "rgb(var(--ink-subtle-rgb) / <alpha-value>)",
        },
        ok: "rgb(var(--ok-rgb) / <alpha-value>)",
        warn: "rgb(var(--warn-rgb) / <alpha-value>)",
        danger: "rgb(var(--danger-rgb) / <alpha-value>)",
        info: "rgb(var(--info-rgb) / <alpha-value>)",
      },
      borderRadius: { xl: "var(--radius-lg)", lg: "var(--radius)", md: "var(--radius-sm)", sm: "var(--radius-xs)" },
      boxShadow: { card: "var(--shadow-card)", pop: "var(--shadow-pop)", focus: "0 0 0 3px var(--focus-ring)" },
      fontFamily: {
        sans: ["Inter", "IBM Plex Sans Arabic", "system-ui", "sans-serif"],
        arabic: ["IBM Plex Sans Arabic", "Inter", "system-ui", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "monospace"],
      },
      fontSize: { "2xs": ["0.6875rem", { lineHeight: "1rem" }] },
      keyframes: {
        shimmer: { "0%": { opacity: "0.45" }, "50%": { opacity: "1" }, "100%": { opacity: "0.45" } },
        slideIn: { from: { transform: "translateY(6px)", opacity: "0" }, to: { transform: "none", opacity: "1" } },
      },
      animation: { shimmer: "shimmer 1.4s ease-in-out infinite", slideIn: "slideIn 180ms ease-out" },
    },
  },
  plugins: [],
} satisfies Config;
