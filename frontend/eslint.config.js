import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import jsxA11y from "eslint-plugin-jsx-a11y";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules", "coverage"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.browser, ...globals.node },
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
      "jsx-a11y": jsxA11y,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      ...jsxA11y.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      "@typescript-eslint/no-explicit-any": "error",
      "jsx-a11y/no-autofocus": "off",
    },
  },
  {
    // Documented exceptions: the design-system module and the session provider
    // intentionally co-locate a few hooks/helpers with their components.
    files: ["src/components/ui.tsx", "src/lib/session.tsx", "src/components/DataTable.tsx"],
    rules: { "react-refresh/only-export-components": "off" },
  },
  {
    // Test files run under Node globals.
    files: ["**/*.test.{ts,tsx}", "vitest.setup.ts"],
    languageOptions: { globals: { ...globals.node } },
  },
);
