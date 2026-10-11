import js from "@eslint/js";
import tseslint from "typescript-eslint";

const nodeGlobals = { console: "readonly", process: "readonly", URL: "readonly", structuredClone: "readonly" };

export default tseslint.config(
  { ignores: ["**/dist/**", "**/generated/**", "**/node_modules/**", "**/.venv/**", "playwright-report/**", "test-results/**"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    languageOptions: { globals: nodeGlobals },
    rules: {
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_", ignoreRestSiblings: true },
      ],
    },
  },
);
