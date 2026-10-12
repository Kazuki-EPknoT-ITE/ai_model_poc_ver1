// Electron Main（設計書 §2.1・§4.3）: ウィンドウ・トレイ・通知・スリープ抑止・認証・Runtime ホストの管理。
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { isAbsolute, join } from "node:path";
import { pathToFileURL } from "node:url";
import { BrowserWindow, Menu, Notification, Tray, app, dialog, ipcMain, nativeImage, powerSaveBlocker, shell } from "electron";
import type { RuntimeCommand, RuntimeEvent } from "@lla/protocol";
import { RuntimeHost } from "./runtime-host.js";
import { SecureStore } from "./secure-store.js";
import type { PluginConfig } from "../shared/ipc.js";
import { canOpen } from "../shared/open-policy.js";
import { Updater } from "./updater.js";

// 試験では userData を差し替える（LLA_USER_DATA_DIR）
if (process.env.LLA_USER_DATA_DIR) app.setPath("userData", process.env.LLA_USER_DATA_DIR);
const E2E = process.env.LLA_E2E === "1";

if (!app.requestSingleInstanceLock()) app.quit();

const DIST = join(__dirname, "..");
const ASSETS = join(DIST, "..", "assets");
const userData = app.getPath("userData");
const dataDir = join(userData, "LocalLLMAgent");
mkdirSync(dataDir, { recursive: true });
const secure = new SecureStore(join(dataDir, "secure"));

interface Settings {
  serverUrl: string;
  /** 予備機（OPS-03）。空なら切り替えない */
  spareUrl: string;
  autoStart: boolean;
}
const SETTINGS_FILE = join(dataDir, "settings.json");
function loadSettings(): Settings {
  const defaults: Settings = {
    serverUrl: process.env.LLM_GATEWAY_URL ?? "http://127.0.0.1:8787",
    spareUrl: process.env.LLM_GATEWAY_SPARE_URL ?? "",
    autoStart: !E2E,
  };
  try {
    return { ...defaults, ...(JSON.parse(readFileSync(SETTINGS_FILE, "utf8")) as Partial<Settings>), ...(process.env.LLM_GATEWAY_URL ? { serverUrl: process.env.LLM_GATEWAY_URL } : {}) };
  } catch {
    return defaults;
  }
}
let settings = loadSettings();

let win: BrowserWindow | null = null;
let tray: Tray | null = null;
let quitting = false;
let host: RuntimeHost | null = null;
let session: { access: string; refresh: string; user: { display_name: string; role: string } } | null = null;
let blockerId: number | null = null;
const activeRuns = new Set<string>();

// ------------------------------------------------------------------ 自動更新（ADM-05）
const updater = new Updater({
  bases: () => serverUrls(),
  token: () => session?.access ?? null,
  dir: join(dataDir, "updates"),
  currentVersion: app.getVersion(),
  onReady: (info) => win?.webContents.send("update:ready", { version: info.version, notes: info.notes }),
  log: (m) => console.warn(m),
  dryRun: process.env.LLA_UPDATE_DRY_RUN === "1",
});
let updateTimer: NodeJS.Timeout | null = null;
function scheduleUpdateChecks(): void {
  void updater.check();
  updateTimer ??= setInterval(() => void updater.check(), 6 * 3600_000);
}

// ------------------------------------------------------------------ 認証
const serverUrls = () => [settings.serverUrl, settings.spareUrl].filter(Boolean);

/** 本番機に繋がらなければ予備機に送る（OPS-03）。HTTP のエラーはそのまま返す（切り替えるのは接続できないときだけ）。 */
async function fetchAny(path: string, init: RequestInit): Promise<Response> {
  let last: unknown = null;
  for (const base of serverUrls()) {
    try {
      return await fetch(`${base}${path}`, init);
    } catch (e) {
      last = e;
    }
  }
  throw last instanceof Error ? last : new Error("fetch failed");
}

async function gatewayLogin(login: string, password: string) {
  const r = await fetchAny(`/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ login, password, device_name: `desktop-${process.env.COMPUTERNAME ?? "pc"}` }),
  });
  const body = (await r.json().catch(() => ({}))) as {
    access_token?: string;
    refresh_token?: string;
    user?: { display_name: string; role: string };
    error?: { code?: string; message?: string };
  };
  if (!r.ok || !body.access_token) throw new Error(body.error?.message ?? `ログインに失敗しました（HTTP ${r.status}）`);
  return { access: body.access_token, refresh: body.refresh_token!, user: body.user! };
}

async function restoreSession(): Promise<boolean> {
  const saved = secure.get("session");
  if (!saved) return false;
  try {
    const s = JSON.parse(saved) as { refresh: string; user: { display_name: string; role: string } };
    const r = await fetchAny(`/v1/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: s.refresh }),
    });
    if (!r.ok) return false;
    const b = (await r.json()) as { access_token: string; refresh_token: string; user?: { display_name: string; role: string } };
    session = { access: b.access_token, refresh: b.refresh_token, user: b.user ?? s.user };
    secure.set("session", JSON.stringify({ refresh: session.refresh, user: session.user }));
    return true;
  } catch {
    return false; // サーバーに繋がらない場合はログイン画面から（接続エラーを表示）
  }
}

// ------------------------------------------------------------------ Runtime ホスト
// 配布版は resources に Node 本体・Runtime・office-tools を同梱する（ADR-001 改訂、T-46）
const RES = process.resourcesPath;

/**
 * プラグイン（AGT-09）の設定を読む。利用者ではなく管理者が置くファイル（既定 %ProgramData%\LocalLLMAgent\plugins.json。
 * 一般利用者は書き換えられない場所）。形式: { "plugins": [{ "id", "command", "args", "cwd"? }] }。不正な項目は飛ばす。
 */
function loadPlugins(): PluginConfig[] {
  const file = process.env.LLA_PLUGINS_FILE ?? join(process.env.ProgramData ?? "C:\\ProgramData", "LocalLLMAgent", "plugins.json");
  if (!existsSync(file)) return [];
  try {
    const raw = JSON.parse(readFileSync(file, "utf8")) as { plugins?: unknown[] };
    return (raw.plugins ?? []).filter((p): p is PluginConfig => {
      const c = p as Partial<PluginConfig>;
      return typeof c.id === "string" && /^[a-z][a-z0-9_]{1,30}$/.test(c.id) && typeof c.command === "string" && Array.isArray(c.args) && c.args.every((a) => typeof a === "string");
    });
  } catch {
    return [];
  }
}

function officeCommand(): { command: string; args: string[]; cwd?: string } | null {
  const raw = process.env.LLA_OFFICE_COMMAND;
  if (!raw) {
    const bundled = join(RES, "office-tools", "office-tools.exe");
    return app.isPackaged && existsSync(bundled) ? { command: bundled, args: [] } : null;
  }
  try {
    return JSON.parse(raw) as { command: string; args: string[]; cwd?: string };
  } catch {
    return null;
  }
}

async function startHost(): Promise<void> {
  if (!session || host) return;
  host = new RuntimeHost({
    script: app.isPackaged ? join(RES, "runtime", "host.mjs") : join(DIST, "runtime-host", "host.mjs"),
    nodePath: process.env.LLA_NODE_PATH ?? (app.isPackaged ? join(RES, "node", "node.exe") : "node"),
    init: () => ({
      type: "init",
      gatewayUrls: serverUrls(),
      dbPath: join(dataDir, "runs.db"),
      dbKey: secure.dbKey(),
      appDataDir: dataDir,
      accessToken: session!.access,
      refreshToken: session!.refresh,
      office: officeCommand(),
      plugins: loadPlugins(),
    }),
  });
  host.on("event", onRuntimeEvent);
  scheduleUpdateChecks();
  host.on("status", (s) => win?.webContents.send("runtime:status", s));
  host.on("tokens", (t) => {
    if (!session) return;
    session = { ...session, access: t.accessToken, refresh: t.refreshToken };
    secure.set("session", JSON.stringify({ refresh: session.refresh, user: session.user }));
  });
  host.on("auth_failed", () => win?.webContents.send("runtime:status", { state: "stopped", warnings: ["ログインの有効期限が切れました"] }));
  await host.start();
}

function onRuntimeEvent(ev: RuntimeEvent): void {
  win?.webContents.send("runtime:event", ev);
  const p = ev.payload as Record<string, unknown>;
  if (ev.type === "run.state") {
    const state = String(p.state);
    if (["done", "stopped"].includes(state)) activeRuns.delete(String(p.run_id));
    else activeRuns.add(String(p.run_id));
    updatePowerBlocker();
  }
  if (ev.type === "notify" && Notification.isSupported() && !E2E) {
    const n = new Notification({ title: String(p.title), body: String(p.body) });
    n.on("click", () => showWindow());
    n.show();
  }
}

/** 実行中はスリープを抑止する（RUN-07）。 */
function updatePowerBlocker(): void {
  if (activeRuns.size > 0 && blockerId === null) blockerId = powerSaveBlocker.start("prevent-app-suspension");
  if (activeRuns.size === 0 && blockerId !== null) {
    powerSaveBlocker.stop(blockerId);
    blockerId = null;
  }
}

// ------------------------------------------------------------------ ウィンドウ・トレイ
function createWindow(): void {
  win = new BrowserWindow({
    width: 1360,
    height: 860,
    minWidth: 960,
    minHeight: 600,
    title: "ローカルAIアシスタント",
    icon: join(ASSETS, "icon.png"),
    show: false,
    backgroundColor: "#f6f7f9",
    webPreferences: {
      preload: join(DIST, "preload", "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
    },
  });
  win.setMenuBarVisibility(false);
  void win.loadFile(join(DIST, "renderer", "index.html"));
  win.once("ready-to-show", () => win?.show());
  // 閉じてもトレイに残り、作業は続く（RUN-07）
  win.on("close", (e) => {
    if (!quitting) {
      e.preventDefault();
      win?.hide();
    }
  });
  // 外部リンクはブラウザで開き、アプリ内では遷移させない
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//.test(url)) void shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (e) => e.preventDefault());
}

function showWindow(): void {
  if (!win) createWindow();
  win?.show();
  win?.focus();
}

function createTray(): void {
  tray = new Tray(nativeImage.createFromPath(join(ASSETS, "tray.png")));
  tray.setToolTip("ローカルAIアシスタント");
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: "開く", click: showWindow },
      { type: "separator" },
      { label: "終了", click: () => void quit() },
    ]),
  );
  tray.on("click", showWindow);
}

async function quit(): Promise<void> {
  quitting = true;
  await host?.stop();
  app.quit();
}

// ------------------------------------------------------------------ IPC（preload の main:*）
function registerIpc(): void {
  ipcMain.handle("auth:status", () => ({
    loggedIn: Boolean(session),
    ...(session ? { user: session.user } : {}),
    serverUrl: settings.serverUrl,
  }));
  ipcMain.handle("auth:login", async (_e, p: { login: string; password: string; remember: boolean }) => {
    try {
      const s = await gatewayLogin(p.login, p.password);
      session = s;
      if (p.remember) secure.set("session", JSON.stringify({ refresh: s.refresh, user: s.user }));
      else secure.delete("session");
      await startHost();
      return { ok: true };
    } catch (e) {
      const msg = (e as Error).message;
      return { ok: false, error: /fetch failed|ECONNREFUSED/.test(msg) ? "サーバーに接続できません" : msg };
    }
  });
  ipcMain.handle("auth:logout", async () => {
    if (session) {
      await fetchAny(`/v1/auth/logout`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${session.access}` },
        body: JSON.stringify({ refresh_token: session.refresh }),
      }).catch(() => undefined);
    }
    session = null;
    secure.delete("session");
    await host?.stop();
    host = null;
  });
  ipcMain.handle("runtime:send", async (_e, command: RuntimeCommand) => {
    if (!host) throw new Error("ログインしてください");
    return host.command(command);
  });
  ipcMain.handle("dialog:pickFolder", async () => {
    const r = await dialog.showOpenDialog(win!, { properties: ["openDirectory"], title: "作業フォルダを選ぶ" });
    return r.canceled ? null : (r.filePaths[0] ?? null);
  });
  ipcMain.handle("shell:openPath", async (_e, p: string, opts?: { page?: number }) => {
    if (!isAbsolute(p) || !existsSync(p)) return { ok: false, error: "ファイルが見つかりません" };
    if (!canOpen(p)) return { ok: false, error: "実行ファイル・マクロ付きの文書は、安全のためここからは開けません" };
    // PDF は該当ページで開く（PDF-07。既定の PDF ビューアーがページ指定に対応していれば、そのページが開く）
    if (opts?.page && /\.pdf$/i.test(p)) {
      await shell.openExternal(`${pathToFileURL(p).href}#page=${Math.floor(opts.page)}`);
      return { ok: true };
    }
    const err = await shell.openPath(p);
    return err ? { ok: false, error: err } : { ok: true };
  });
  ipcMain.handle("workspace:list", (_e, dir: string) => {
    if (!isAbsolute(dir) || !existsSync(dir)) return [];
    return readdirSync(dir, { withFileTypes: true })
      .filter((d) => !d.name.startsWith(".") && !d.name.startsWith("~$"))
      .sort((a, b) => Number(b.isDirectory()) - Number(a.isDirectory()) || a.name.localeCompare(b.name, "ja"))
      .slice(0, 500)
      .map((d) => ({ name: d.name, path: join(dir, d.name), dir: d.isDirectory() }));
  });
  ipcMain.handle("settings:get", () => settings);
  ipcMain.handle("settings:set", (_e, patch: Partial<Settings>) => {
    settings = { ...settings, ...patch };
    writeFileSync(SETTINGS_FILE, JSON.stringify(settings, null, 2));
    if (!E2E) app.setLoginItemSettings({ openAtLogin: settings.autoStart });
  });
  ipcMain.handle("app:quit", () => quit());
  ipcMain.handle("update:status", () => (updater.ready ? { version: updater.ready.version, notes: updater.ready.notes } : null));
  ipcMain.handle("update:install", async () => {
    // 作業中はインストールしない（終わってから。作業は Run Store に残るので、更新後に再開もできる）
    if (activeRuns.size > 0) return { ok: false, error: "作業中です。作業が終わってから更新してください" };
    try {
      updater.install();
    } catch (e) {
      return { ok: false, error: (e as Error).message };
    }
    if (process.env.LLA_UPDATE_DRY_RUN !== "1") await quit();
    return { ok: true };
  });
  ipcMain.handle("app:version", () => app.getVersion());
}

app.on("second-instance", showWindow);
app.on("window-all-closed", () => {
  /* トレイに常駐する */
});
app.on("before-quit", () => {
  quitting = true;
});

void app.whenReady().then(async () => {
  registerIpc();
  createTray();
  if (await restoreSession()) await startHost().catch(() => undefined);
  createWindow();
});
