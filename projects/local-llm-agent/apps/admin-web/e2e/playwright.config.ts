import { defineConfig } from "@playwright/test";

// 管理画面の E2E。モック Gateway がビルド済みの管理画面を /admin で配る（本物の Gateway と同じ配置）
const PORT = Number(process.env.ADMIN_E2E_PORT ?? 8788);
export default defineConfig({
  testDir: ".",
  testMatch: /.*\.e2e\.ts$/,
  outputDir: process.env.E2E_ARTIFACTS ?? "../../test-results/admin-web",
  timeout: 60_000,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: "retain-on-failure" },
  webServer: {
    // ADMIN_E2E_REAL=1 なら本物の Gateway（FakeEngine・SQLite・毎回新しい一時フォルダ）を相手にする
    command:
      process.env.ADMIN_E2E_REAL === "1"
        ? `uv run python server/gateway/scripts/dev_server.py --port ${PORT}`
        : `uv run mock-gateway --scenario eval/mock-gateway/scenarios/smoke.yaml --port ${PORT}`,
    cwd: "../../..",
    url: `http://127.0.0.1:${PORT}/healthz`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
