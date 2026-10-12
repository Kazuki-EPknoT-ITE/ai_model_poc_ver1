import { execFileSync, spawn, type ChildProcess } from "node:child_process";
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

/** Windows では uv の子プロセス（Python）まで止めないとファイルが使用中のまま残る */
function killTree(proc: ChildProcess): void {
  if (process.platform === "win32" && proc.pid) {
    try {
      execFileSync("taskkill", ["/PID", String(proc.pid), "/T", "/F"], { stdio: "ignore" });
      return;
    } catch {
      /* 既に終了している */
    }
  }
  proc.kill();
}

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
  /** Gateway（モックまたは本物）の URL */
  url: string;
  close(): Promise<void>;
}

/** 本物の Gateway（FakeEngine・SQLite）を一時フォルダで起動する（server/gateway/scripts/dev_server.py） */
export async function startRealGateway(dir: string): Promise<{ url: string; proc: ChildProcess }> {
  const port = await freePort();
  const proc = spawn("uv", ["run", "--project", PROJECT, "python", join(PROJECT, "server", "gateway", "scripts", "dev_server.py"), "--port", String(port), "--dir", join(dir, "gateway")], {
    cwd: PROJECT,
    stdio: "ignore",
    windowsHide: true,
  });
  const url = `http://127.0.0.1:${port}`;
  for (let i = 0; ; i++) {
    try {
      if ((await fetch(`${url}/healthz`)).ok) return { url, proc };
    } catch {
      /* 起動待ち */
    }
    if (i > 600) throw new Error("real gateway did not start");
    await new Promise((r) => setTimeout(r, 200));
  }
}

export async function launch(scenario: string, opts: { office?: boolean; realGateway?: boolean } = {}): Promise<Ctx> {
  const dir = mkdtempSync(join(tmpdir(), "lla-e2e-"));
  const ws = join(dir, "ws");
  const outside = join(dir, "outside");
  for (const d of [ws, outside, join(dir, "userdata")]) mkdirSync(d);
  if (opts.realGateway) {
    const real = await startRealGateway(dir);
    return launchApp(dir, ws, outside, real.url, real.proc, opts);
  }
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
  return launchApp(dir, ws, outside, url, mock, opts);
}

async function launchApp(dir: string, ws: string, outside: string, url: string, mock: ChildProcess, opts: { office?: boolean }): Promise<Ctx> {
  // LLA_E2E_APP_EXE があれば、配布物（release/win-unpacked）をそのまま起動する（同梱の Node・office-tools を使う）
  const packaged = process.env.LLA_E2E_APP_EXE;
  const electronPath = packaged ?? ((await import("electron")).default as unknown as string);
  const app = await _electron.launch({
    executablePath: electronPath,
    args: packaged ? [] : [APP_DIR],
    cwd: APP_DIR,
    env: {
      ...process.env,
      LLA_USER_DATA_DIR: join(dir, "userdata"),
      LLM_GATEWAY_URL: url,
      LLA_E2E: "1",
      // LLA_E2E_OFFICE_EXE があれば、PyInstaller でまとめた office-tools.exe を使う（配布物と同じ形で試験する）
      ...(opts.office && !packaged
        ? {
            LLA_OFFICE_COMMAND: JSON.stringify(
              process.env.LLA_E2E_OFFICE_EXE
                ? { command: process.env.LLA_E2E_OFFICE_EXE, args: [] }
                : { command: "uv", args: ["run", "--project", PROJECT, "office-tools"], cwd: PROJECT },
            ),
          }
        : {}),
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
    url,
    close: async () => {
      await app.evaluate(({ app: a }) => a.exit(0)).catch(() => undefined);
      await app.close().catch(() => undefined);
      killTree(mock);
      await new Promise((r) => setTimeout(r, 800));
      rmSync(dir, { recursive: true, force: true, maxRetries: 5, retryDelay: 300 });
    },
  };
}

export async function login(page: Page, id = "dev", password = "dev"): Promise<void> {
  await page.getByLabel("ログインID").fill(id);
  await page.getByLabel("パスワード").fill(password);
  await page.getByRole("button", { name: "ログイン" }).click();
  await page.getByRole("button", { name: /新しい会話/ }).waitFor();
}
