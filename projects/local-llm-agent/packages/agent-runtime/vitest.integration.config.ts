import { defineConfig } from "vitest/config";

// 結合試験（I）: モック Gateway（uv run mock-gateway）を起動して Runtime を動かす
export default defineConfig({
  test: { include: ["test/integration/**/*.test.ts"], testTimeout: 120_000, hookTimeout: 120_000, fileParallelism: false },
});
