import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// 互換性の試験（本物の Gateway ＋ 既存の TS クライアント）。pnpm test には含めない。
const here = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  root: here,
  cacheDir: resolve(here, "../../../../node_modules/.vite-gateway-compat"),
  test: { include: ["*.test.ts"], testTimeout: 60_000, hookTimeout: 120_000 },
});
