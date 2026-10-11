import { defineConfig } from "@playwright/test";

// デスクトップアプリの E2E（Playwright _electron）。モック Gateway は各試験で起動する。
export default defineConfig({
  testDir: ".",
  testMatch: /.*\.e2e\.ts$/,
  outputDir: process.env.E2E_ARTIFACTS ?? "../../test-results/desktop",
  timeout: 120_000,
  workers: 1,
  reporter: [["list"]],
  use: { trace: "retain-on-failure" },
});
