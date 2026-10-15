// 結合試験（I）の土台: モック Gateway（Python）を起動し、Runtime を組み立てる。
import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { RuntimeEvent } from "@lla/protocol";
import { AgentRuntime, type RuntimeOptions } from "../../src/runtime.js";
import { GatewayClient, type TokenProvider } from "../../src/gateway/client.js";
import { RunStore } from "../../src/store/store.js";
import { ToolRouter } from "../../src/tools/router.js";
import type { McpToolHost } from "../../src/tools/mcp.js";

export const PROJECT = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");
export const SCENARIOS = join(PROJECT, "eval", "mock-gateway", "scenarios", "runtime");

/** Windows では uv の子プロセス（Python のモック Gateway）まで止める。proc.kill() だけだと uv しか止まらず、モックが残り続ける */
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

export interface MockGateway {
  url: string;
  proc: ChildProcess;
  record: string;
  /** 推論の入力と出力の組（schema_tools: 文法で選べたツール） */
  samples: string;
  stop(): Promise<void>;
}

export async function startMock(scenario: string, dir: string): Promise<MockGateway> {
  const port = await freePort();
  const record = join(dir, "requests.jsonl");
  const samples = join(dir, "samples.jsonl");
  const proc = spawn(
    "uv",
    ["run", "--project", PROJECT, "mock-gateway", "--scenario", join(SCENARIOS, scenario), "--port", String(port), "--ping-interval", "0.2", "--record", record, "--record-samples", samples],
    { cwd: PROJECT, stdio: "ignore", windowsHide: true },
  );
  const url = `http://127.0.0.1:${port}`;
  const deadline = Date.now() + 60_000;
  for (;;) {
    try {
      if ((await fetch(`${url}/healthz`)).ok) break;
    } catch {
      /* 起動待ち */
    }
    if (Date.now() > deadline) throw new Error("mock gateway did not start");
    await new Promise((r) => setTimeout(r, 200));
  }
  return {
    url,
    proc,
    record,
    samples,
    stop: async () => {
      killTree(proc);
      await new Promise((r) => setTimeout(r, 200));
    },
  };
}

export function loginProvider(url: string): TokenProvider {
  let token: string | null = null;
  const login = async () => {
    const r = await fetch(`${url}/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ login: "dev", password: "dev", device_name: "vitest" }),
    });
    token = ((await r.json()) as { access_token: string }).access_token;
    return token;
  };
  return { accessToken: async () => token ?? login(), refresh: login };
}

export interface Rig {
  dir: string;
  ws: string;
  outside: string;
  appData: string;
  store: RunStore;
  runtime: AgentRuntime;
  events: RuntimeEvent[];
  mock: MockGateway;
  close(): Promise<void>;
  /** 同じ Run Store で Runtime を作り直す（プロセスの再起動を模擬する） */
  restart(): Promise<void>;
}

export async function makeRig(
  scenario: string,
  over: Partial<RuntimeOptions> = {},
  office?: (gateway: GatewayClient) => McpToolHost[],
): Promise<Rig> {
  const dir = mkdtempSync(join(tmpdir(), "lla-it-"));
  const ws = join(dir, "ws");
  const outside = join(dir, "outside");
  const appData = join(dir, "appdata");
  for (const d of [ws, outside, appData]) mkdirSync(d);
  const mock = await startMock(scenario, dir);
  const events: RuntimeEvent[] = [];
  const build = (store: RunStore) => {
    const gateway = new GatewayClient(mock.url, loginProvider(mock.url), { netLevel: () => store.getSetting("net_level", "off") });
    return new AgentRuntime({
      store,
      gateway,
      router: new ToolRouter(store, gateway, office?.(gateway)[0] ?? null),
      appDataDir: appData,
      emit: (e) => events.push(e),
      retryBaseMs: 20,
      lockWaitMs: 500,
      ...over,
    });
  };
  const rig: Rig = {
    dir,
    ws,
    outside,
    appData,
    store: RunStore.open(join(appData, "runs.db"), "it-key"),
    runtime: null as unknown as AgentRuntime,
    events,
    mock,
    close: async () => {
      await rig.runtime.shutdown();
      rig.store.close();
      await mock.stop();
      rmSync(dir, { recursive: true, force: true });
    },
    restart: async () => {
      await rig.runtime.shutdown();
      rig.store.close();
      rig.store = RunStore.open(join(appData, "runs.db"), "it-key");
      rig.runtime = build(rig.store);
      await rig.runtime.start();
    },
  };
  rig.runtime = build(rig.store);
  await rig.runtime.start();
  return rig;
}

export async function waitFor<T>(fn: () => T | undefined | false, timeoutMs = 20_000, label = "condition"): Promise<T> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const v = fn();
    if (v) return v;
    if (Date.now() > deadline) throw new Error(`timeout waiting for ${label}`);
    await new Promise((r) => setTimeout(r, 50));
  }
}

let cmdId = 0;
export function cmd(type: string, payload: Record<string, unknown>) {
  return { v: 1 as const, id: `c${++cmdId}`, type: type as never, payload };
}

/** 失敗時の調査用: イベント・手順・注記・モックへの要求を書き出す。 */
export function dump(rig: Rig, runId: string): string {
  const { readFileSync } = require_fs();
  const ev = rig.events.map((e) => `${e.type} ${JSON.stringify(e.payload).slice(0, 200)}`).join("\n");
  const steps = JSON.stringify(rig.store.db.prepare("SELECT seq, phase, tool, error_code, substr(result,1,300) r FROM steps WHERE run_id = ?").all(runId), null, 1);
  const notes = JSON.stringify(rig.store.db.prepare("SELECT substr(content,1,300) c FROM messages WHERE role = 'system_note'").all());
  let reqs = "";
  try {
    reqs = readFileSync(rig.mock.record, "utf8").split("\n").slice(-30).join("\n");
  } catch {
    /* 記録なし */
  }
  return `EVENTS\n${ev}\nSTEPS\n${steps}\nNOTES\n${notes}\nREQS\n${reqs}`;
}
import * as fsMod from "node:fs";
function require_fs() {
  return fsMod;
}
