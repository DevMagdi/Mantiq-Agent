import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
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
          sunken: "rgb(var(--surface-sunken-rgb) / <alpha-value>)",
          alt: "rgb(var(--surface-alt-rgb) / <alpha-value>)",
          raised: "rgb(var(--surface-raised-rgb) / <alpha-value>)",
        },
        sidebar: {
          DEFAULT: "rgb(var(--sidebar-rgb) / <alpha-value>)",
          raised: "rgb(var(--sidebar-raised-rgb) / <alpha-value>)",
          line: "rgb(var(--sidebar-line-rgb) / <alpha-value>)",
          ink: "rgb(var(--sidebar-ink-rgb) / <alpha-value>)",
          muted: "rgb(var(--sidebar-ink-muted-rgb) / <alpha-value>)",
        },
        line: {
          DEFAULT: "rgb(var(--line-rgb) / <alpha-value>)",
          strong: "rgb(var(--line-strong-rgb) / <alpha-value>)",
        },
        ink: {
          DEFAULT: "rgb(var(--ink-rgb) / <alpha-value>)",
          muted: "rgb(var(--ink-muted-rgb) / <alpha-value>)",
          subtle: "rgb(var(--ink-subtle-rgb) / <alpha-value>)",
        },
        ok: { DEFAULT: "rgb(var(--ok-rgb) / <alpha-value>)", ink: "rgb(var(--ok-ink-rgb) / <alpha-value>)" },
        warn: { DEFAULT: "rgb(var(--warn-rgb) / <alpha-value>)", ink: "rgb(var(--warn-ink-rgb) / <alpha-value>)" },
        danger: { DEFAULT: "rgb(var(--danger-rgb) / <alpha-value>)", ink: "rgb(var(--danger-ink-rgb) / <alpha-value>)" },
        info: { DEFAULT: "rgb(var(--info-rgb) / <alpha-value>)", ink: "rgb(var(--info-ink-rgb) / <alpha-value>)" },
      },
      borderRadius: { xl: "var(--radius-lg)", lg: "var(--radius)", md: "var(--radius-sm)", sm: "var(--radius-xs)" },
      spacing: {
        sidebar: "var(--sidebar-width)",
        "sidebar-collapsed": "var(--sidebar-width-collapsed)",
        header: "var(--header-height)",
      },
      boxShadow: {
        1: "var(--shadow-1)",
        2: "var(--shadow-2)",
        3: "var(--shadow-3)",
        card: "var(--shadow-card)",
        pop: "var(--shadow-pop)",
        focus: "0 0 0 3px var(--focus-ring)",
      },
      fontFamily: {
        sans: ["Inter", "IBM Plex Sans Arabic", "system-ui", "sans-serif"],
        arabic: ["IBM Plex Sans Arabic", "Inter", "system-ui", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "monospace"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
        "3xs": ["0.625rem", { lineHeight: "0.875rem" }],
      },
      transitionTimingFunction: { out: "var(--ease-out)" },
      keyframes: {
        shimmer: { "0%": { opacity: "0.45" }, "50%": { opacity: "1" }, "100%": { opacity: "0.45" } },
        slideIn: { from: { transform: "translateY(6px)", opacity: "0" }, to: { transform: "none", opacity: "1" } },
        fadeIn: { from: { opacity: "0" }, to: { opacity: "1" } },
      },
      animation: {
        shimmer: "shimmer 1.4s ease-in-out infinite",
        slideIn: "slideIn 180ms var(--ease-out)",
        fadeIn: "fadeIn 140ms var(--ease-out)",
      },
    },
  },
  plugins: [],
} satisfies Config;
