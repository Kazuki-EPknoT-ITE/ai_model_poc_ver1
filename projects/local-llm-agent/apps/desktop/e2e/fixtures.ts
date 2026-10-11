import { spawn, type ChildProcess } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { _electron, type ElectronApplication, type Page } from "@playwright/test";

const HERE = dirname(fileURLToPath(import.meta.url));
export const APP_DIR = resolve(HERE, "..");
export const PROJECT = resolve(APP_DIR, "../..");
export const ARTIFACTS = process.env.E2E_ARTIFACTS ?? join(PROJECT, "test-results", "desktop");

async function freePort(): Promise<number> {
  return new Promise((res) => {
    const s = createServer();
    s.listen(0, "127.0.0.1", () => {
      const port = (s.address() as { port: number }).port;
      s.close(() => res(port));
    });
  });
}

export interface Ctx {
  app: ElectronApplication;
  page: Page;
  dir: string;
  ws: string;
  outside: string;
  mock: ChildProcess;
  close(): Promise<void>;
}

export async function launch(scenario: string, opts: { office?: boolean } = {}): Promise<Ctx> {
  const dir = mkdtempSync(join(tmpdir(), "lla-e2e-"));
  const ws = join(dir, "ws");
  const outside = join(dir, "outside");
  for (const d of [ws, outside, join(dir, "userdata")]) mkdirSync(d);
  const port = await freePort();
  const mock = spawn(
    "uv",
    [
      "run",
      "--project",
      PROJECT,
      "mock-gateway",
      "--scenario",
      join(PROJECT, "eval", "mock-gateway", "scenarios", "runtime", scenario),
      "--port",
      String(port),
      "--ping-interval",
      "0.2",
    ],
    { cwd: PROJECT, stdio: "ignore", windowsHide: true },
  );
  const url = `http://127.0.0.1:${port}`;
  for (let i = 0; ; i++) {
    try {
      if ((await fetch(`${url}/healthz`)).ok) break;
    } catch {
      /* 起動待ち */
    }
    if (i > 300) throw new Error("mock gateway did not start");
    await new Promise((r) => setTimeout(r, 200));
  }
  const electronPath = (await import("electron")).default as unknown as string;
  const app = await _electron.launch({
    executablePath: electronPath,
    args: [APP_DIR],
    cwd: APP_DIR,
    env: {
      ...process.env,
      LLA_USER_DATA_DIR: join(dir, "userdata"),
      LLM_GATEWAY_URL: url,
      LLA_E2E: "1",
      ...(opts.office ? { LLA_OFFICE_COMMAND: JSON.stringify({ command: "uv", args: ["run", "--project", PROJECT, "office-tools"], cwd: PROJECT }) } : {}),
    },
  });
  // フォルダ選択ダイアログは自動操作できないので、Main 側で差し替える
  await app.evaluate(({ dialog }, folder) => {
    dialog.showOpenDialog = (async () => ({ canceled: false, filePaths: [folder] })) as typeof dialog.showOpenDialog;
  }, ws);
  const page = await app.firstWindow();
  return {
    app,
    page,
    dir,
    ws,
    outside,
    mock,
    close: async () => {
      await app.evaluate(({ app: a }) => a.exit(0)).catch(() => undefined);
      await app.close().catch(() => undefined);
      mock.kill();
      await new Promise((r) => setTimeout(r, 800));
      rmSync(dir, { recursive: true, force: true, maxRetries: 5, retryDelay: 300 });
    },
  };
}

export async function login(page: Page): Promise<void> {
  await page.getByLabel("ログインID").fill("dev");
  await page.getByLabel("パスワード").fill("dev");
  await page.getByRole("button", { name: "ログイン" }).click();
  await page.getByRole("button", { name: /新しい会話/ }).waitFor();
}
