import { defineConfig } from "@playwright/test";

// 現時点の E2E 対象はモック Gateway（デスクトップアプリは M4 で追加する）。
const PORT = Number(process.env.MOCK_GATEWAY_PORT ?? 8787);
const ARTIFACTS = process.env.E2E_ARTIFACTS ?? "test-results";

export default defineConfig({
  testDir: ".",
  outputDir: ARTIFACTS,
  timeout: 60_000,
  reporter: [["list"]],
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: "retain-on-failure" },
  webServer: {
    command: `uv run mock-gateway --scenario eval/mock-gateway/scenarios/smoke.yaml --port ${PORT} --ping-interval 1`,
    cwd: "..",
    url: `http://127.0.0.1:${PORT}/healthz`,
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
