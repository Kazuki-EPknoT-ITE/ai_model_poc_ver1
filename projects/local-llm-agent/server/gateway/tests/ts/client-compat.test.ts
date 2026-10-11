// 互換性の試験: 本物の Gateway（FakeEngine・SQLite）を起動し、既存の TS クライアント（GatewayClient）から使う。
// 実行: projects/local-llm-agent で `pnpm exec vitest run --config server/gateway/tests/ts/vitest.config.ts`
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import {
  GatewayClient,
  GatewayError,
  type TokenProvider,
} from "../../../../packages/agent-runtime/src/gateway/client.js";

const PROJECT = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");
const PYTHON = join(PROJECT, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const PASSWORD = "compat-pass-1";
const OUTPUT = JSON.stringify({
  type: "final",
  summary: "互換性の確認です",
  outputs: [],
  failures: [],
  needs_review: [],
});

let dir = "";
let url = "";
let proc: ChildProcess | null = null;
let env: NodeJS.ProcessEnv = {};

async function freePort(): Promise<number> {
  return new Promise((res) => {
    const s = createServer();
    s.listen(0, "127.0.0.1", () => {
      const port = (s.address() as { port: number }).port;
      s.close(() => res(port));
    });
  });
}

function cli(args: string[], extra: NodeJS.ProcessEnv = {}): void {
  const r = spawnSync(PYTHON, ["-m", "llm_gateway.cli", ...args], {
    cwd: PROJECT,
    env: { ...env, ...extra },
    encoding: "utf-8",
  });
  if (r.status !== 0) throw new Error(`llm-gateway ${args[0]} failed: ${r.stderr}`);
}

function provider(initial: string | null = null): TokenProvider & { refreshed: number } {
  let token = initial;
  const p = {
    refreshed: 0,
    async login(): Promise<string> {
      const r = await fetch(`${url}/v1/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ login: "dev", password: PASSWORD, device_name: "vitest" }),
      });
      if (!r.ok) throw new Error(`login failed ${r.status}`);
      token = ((await r.json()) as { access_token: string }).access_token;
      return token;
    },
    async accessToken(): Promise<string> {
      return token ?? p.login();
    },
    async refresh(): Promise<string> {
      p.refreshed += 1;
      return p.login();
    },
  };
  return p;
}

beforeAll(async () => {
  dir = mkdtempSync(join(tmpdir(), "llmgw-compat-"));
  env = {
    ...process.env,
    PYTHONIOENCODING: "utf-8",
    LLMGW_JWT_PRIVATE_KEY_FILE: join(dir, "keys", "jwt_ed25519.pem"),
    LLMGW_PAYLOAD_KEYS_FILE: join(dir, "keys", "payload_keys.json"),
  };
  const config = join(dir, "gateway.yaml");
  writeFileSync(
    config,
    [
      `database: { url: "sqlite+aiosqlite:///${join(dir, "gw.db").replaceAll("\\", "/")}" }`,
      "auth: { argon2: { memory_kib: 1024, time_cost: 1, parallelism: 1 } }",
      "jobs: { ping_interval_s: 0.2, token_flush_ms: 0 }",
      "scheduler: { tick_s: 0.05 }",
      "models:",
      "  - id: local-12b",
      "    label: 高速",
      "    context_budget: 2000",
      `    engine: { kind: fake, fake_token_delay_s: 0.005, fake_output: '${OUTPUT}' }`,
      "",
    ].join("\n"),
    "utf-8",
  );
  cli(["keygen", "--out-dir", join(dir, "keys")]);
  cli(["create-user", "--config", config, "--login", "dev"], { LLMGW_NEW_PASSWORD: PASSWORD });
  const port = await freePort();
  url = `http://127.0.0.1:${port}`;
  proc = spawn(
    PYTHON,
    [
      "-m",
      "llm_gateway.cli",
      "serve",
      "--config",
      config,
      "--host",
      "127.0.0.1",
      "--port",
      String(port),
      "--log-level",
      "warning",
    ],
    {
      cwd: PROJECT,
      env,
      stdio: "ignore",
      windowsHide: true,
    },
  );
  const deadline = Date.now() + 60_000;
  for (;;) {
    try {
      if ((await fetch(`${url}/healthz`)).ok) break;
    } catch {
      /* 起動待ち */
    }
    if (Date.now() > deadline) throw new Error("gateway did not start");
    await new Promise((r) => setTimeout(r, 200));
  }
});

afterAll(async () => {
  proc?.kill();
  await new Promise((r) => setTimeout(r, 300));
  rmSync(dir, { recursive: true, force: true });
});

describe("GatewayClient ⇔ 本物の Gateway", () => {
  it("config を読める", async () => {
    const client = new GatewayClient(url, provider());
    const cfg = await client.config();
    expect(cfg.protocol_version).toBe("1.0");
    expect(cfg.models[0]).toMatchObject({ id: "local-12b", available: true, context_budget: 2000 });
    expect(cfg.net.level_cap).toBe("off");
    expect(cfg.limits.max_concurrent).toBe(2);
  });

  it("infer: 投入 → SSE（queue・token・done）を受け取る。同じキーなら同じジョブ", async () => {
    const client = new GatewayClient(url, provider());
    const tokens: string[] = [];
    const queue: number[] = [];
    let jobId = "";
    const req = {
      model: "local-12b",
      messages: [
        { role: "system" as const, content: "あなたは事務作業のエージェントです" },
        { role: "user" as const, content: "請求書を集計して" },
      ],
      response_format: { type: "json_schema" as const, schema: { type: "object" } },
      max_tokens: 256,
      temperature: 0.2,
      priority_hint: "interactive" as const,
      purpose: "agent" as const,
    };
    const res = await client.infer("run-1:step-1:0", req, {
      onJob: (id) => (jobId = id),
      onQueue: (p) => queue.push(p),
      onToken: (d) => tokens.push(d),
    });
    expect(res.output).toBe(OUTPUT);
    expect(tokens.join("")).toBe(OUTPUT);
    expect(queue.length).toBeGreaterThan(0);
    expect(res.usage.output_tokens).toBeGreaterThan(0);
    expect(jobId).not.toBe("");

    let again = "";
    const res2 = await client.infer("run-1:step-1:0", req, { onJob: (id) => (again = id) });
    expect(again).toBe(jobId);
    expect(res2.output).toBe(OUTPUT);
    // 完了したジョブを最初から読み直す（再接続と同じ経路）
    expect((await client.follow(jobId)).output).toBe(OUTPUT);
  });

  it("文脈予算の超過は 413 CONTEXT_TOO_LARGE", async () => {
    const client = new GatewayClient(url, provider());
    const err = await client
      .infer("run-1:step-2:0", {
        model: "local-12b",
        messages: [{ role: "user", content: "あ".repeat(4000) }],
      })
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(GatewayError);
    expect((err as GatewayError).status).toBe(413);
    expect((err as GatewayError).code).toBe("CONTEXT_TOO_LARGE");
  });

  it("無効なアクセストークンは 401 → refresh して続ける", async () => {
    const tp = provider("not-a-token");
    const client = new GatewayClient(url, tp);
    const res = await client.infer("run-1:step-3:0", {
      model: "local-12b",
      messages: [{ role: "user", content: "hi" }],
    });
    expect(res.output).toBe(OUTPUT);
    expect(tp.refreshed).toBe(1);
  });
});
