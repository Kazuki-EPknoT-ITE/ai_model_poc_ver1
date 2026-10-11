// 障害注入試験（T-51、試験仕様 §4 FI-01〜20）のランナー。CI（開発PC）で回せるものを自動化している。
//   pnpm fi [--only FI-11,FI-12] [--repeat 3] [--out <結果 JSON>]
// 本物のプロセスを使う: Runtime は子プロセス（host.ts）、推論はモック Gateway（Python）。
// 注入: 子プロセスの強制終了（taskkill /F /T）、モック Gateway の停止・再起動、ファイルのロック（tools/lockfile.ps1）。
// 結果は eval/fault-injection/results/latest.json（評価ハーネスの scorer: resilience が読む）。
import { mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync, existsSync } from "node:fs";
import type { ChildProcess } from "node:child_process";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { RuntimeEvent } from "@lla/protocol";
import { RunStore } from "@lla/agent-runtime";
import { PROJECT, killTree, sleep, spawnLines, viteNodeBin, waitUntil } from "../../harness/src/env.js";
import { MockGatewayProc } from "../../harness/src/mock.js";
import type { HostConfig } from "./host.js";

const FI_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const HOST_TS = join(FI_DIR, "src", "host.ts");
const SCENARIOS = join(FI_DIR, "scenarios");

// ------------------------------------------------------------------ Runtime の子プロセス
class Host {
  readonly events: RuntimeEvent[] = [];
  readonly hooks: { point: string; n: number }[] = [];
  readonly frozen: string[] = [];
  readonly stderr: string[] = [];
  runId: string | null = null;
  done = false;
  private proc: ChildProcess;
  readonly exited: Promise<number>;

  constructor(cfg: HostConfig, freeze?: string) {
    const env: NodeJS.ProcessEnv = { ...process.env, AGENT_TEST_HOOKS: "1", FI_HOST_CONFIG: JSON.stringify(cfg) };
    if (freeze) env.AGENT_TEST_HOOK_FREEZE = freeze;
    else delete env.AGENT_TEST_HOOK_FREEZE;
    this.proc = spawnLines(process.execPath, [viteNodeBin(), HOST_TS], {
      cwd: PROJECT,
      env,
      onLine: (line, stream) => {
        if (stream === "out") {
          if (line.startsWith("@@event ")) this.events.push(JSON.parse(line.slice(8)) as RuntimeEvent);
          else if (line.startsWith("@@run ")) this.runId = line.slice(6).trim();
          else if (line.startsWith("@@done")) this.done = true;
        } else if (line.startsWith("@@hook ")) {
          this.hooks.push(JSON.parse(line.slice(7)) as { point: string; n: number });
        } else if (line.startsWith("@@frozen ")) {
          this.frozen.push(line.slice(9).trim());
        } else if (line.trim()) {
          this.stderr.push(line);
        }
      },
    });
    this.exited = new Promise((res) => this.proc.on("exit", (code) => res(code ?? -1)));
  }

  /** アプリの異常終了（taskkill /F /T）。 */
  async kill(): Promise<void> {
    await killTree(this.proc.pid);
    await this.exited;
  }

  async finish(timeoutMs: number): Promise<void> {
    await waitUntil(() => this.done, timeoutMs, "host done", 100);
    await this.exited;
  }

  states(): string[] {
    return this.events.filter((e) => e.type === "run.state").map((e) => (e.payload as { state: string }).state);
  }
}

// ------------------------------------------------------------------ シナリオ
interface Check {
  name: string;
  ok: boolean;
  detail?: string;
}

interface Ctx {
  dir: string;
  ws: string;
  outside: string;
  appData: string;
  mock: MockGatewayProc;
  hosts: Host[];
  cfg(over?: Partial<HostConfig>): HostConfig;
  host(over?: Partial<HostConfig>, freeze?: string): Host;
  checks: Check[];
  check(name: string, ok: boolean, detail?: string): void;
}

interface Scenario {
  id: string;
  fi: string;
  title: string;
  mock: string;
  timeoutMs: number;
  run(ctx: Ctx): Promise<void>;
}

const REQUEST = "a.txt を out にコピーして";

function lockFile(path: string, seconds: number): Promise<{ proc: ChildProcess; released: Promise<void> }> {
  return new Promise((res, rej) => {
    const proc = spawnLines("powershell", ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", join(FI_DIR, "tools", "lockfile.ps1"), "-Path", path, "-Seconds", String(seconds)], {
      onLine: (l) => {
        if (l.trim() === "locked") res({ proc, released: new Promise((r) => proc.on("exit", () => r())) });
      },
    });
    proc.on("exit", (code) => rej(new Error(`lockfile が終了しました (${code})`)));
  });
}

/** 最後の Run の完了報告の summary（Run Store から読む）。 */
function finalSummary(ctx: Ctx): string | null {
  const store = RunStore.open(join(ctx.appData, "runs.db"), "fi");
  try {
    const row = store.db.prepare("SELECT final_action FROM runs ORDER BY started_at DESC LIMIT 1").get() as { final_action: string | null } | undefined;
    return row?.final_action ? ((JSON.parse(row.final_action) as { summary?: string }).summary ?? null) : null;
  } finally {
    store.close();
  }
}

const read = (p: string) => (existsSync(p) ? readFileSync(p, "utf8") : null);

/** 書き込みシナリオ（write.yaml）の共通の確認: 2件とも元のパスに1回だけ書かれている。 */
function checkWrites(ctx: Ctx): void {
  ctx.check("out/B-OUT.txt = a.txt", read(join(ctx.ws, "out", "B-OUT.txt")) === "A");
  ctx.check("out/C-OUT.txt = a.txt", read(join(ctx.ws, "out", "C-OUT.txt")) === "A");
}

const SCENARIO_LIST: Scenario[] = [
  {
    id: "FI-03",
    fi: "FI-03",
    title: "形式の崩れ（不正な JSON を3回）",
    mock: "format.yaml",
    timeoutMs: 60_000,
    async run(ctx) {
      const h = ctx.host();
      await h.finish(this.timeoutMs);
      ctx.check("差し戻しで回復して完了", finalSummary(ctx) === "回復しました。");
      // 不正な出力3回 ＋ 回復後の1回 = 推論の投入4回以上（注入が実際に起きたこと）
      const posts = ctx.mock.requests().filter((r) => r.method === "POST" && r.path === "/v1/steps").length;
      ctx.check("不正な出力を3回受けた", posts >= 4, `投入 ${posts} 回`);
    },
  },
  {
    id: "FI-05",
    fi: "FI-05",
    title: "迷走（昇格先なし）→ 立て直し2回 → stalled → 低優先度で完了",
    mock: "stall.yaml",
    timeoutMs: 90_000,
    async run(ctx) {
      const h = ctx.host();
      await h.finish(this.timeoutMs);
      const s = h.states();
      ctx.check("立て直し2回", s.filter((x) => x === "replanning").length === 2, s.join(","));
      ctx.check("stalled を通知", h.events.some((e) => e.type === "notify" && (e.payload as { kind: string }).kind === "stalled"));
    },
  },
  {
    id: "FI-06",
    fi: "FI-06",
    title: "サーバーの応答なし（推論中にモック Gateway を落とし、3秒後に起動）",
    mock: "write.yaml",
    timeoutMs: 120_000,
    async run(ctx) {
      const h = ctx.host();
      // 2手目（C-OUT のコピーを決める推論。応答に 1.5 秒かかる）を投入した直後に落とす
      await waitUntil(() => h.hooks.some((x) => x.point === "infer.submitted" && x.n === 2), 60_000, "2nd inference");
      await ctx.mock.stop();
      await sleep(3_000);
      await ctx.mock.start();
      await h.finish(this.timeoutMs);
      ctx.check("retry イベントを出して待った", h.events.some((e) => e.type === "retry"));
      // 落とす前に投入した手の Idempotency-Key で、再起動後も投入している（同じ手として扱う）
      const reqs = ctx.mock.requests().filter((r) => r.method === "POST" && r.path === "/v1/steps");
      const before = new Set(reqs.filter((r) => r.incarnation === 1).map((r) => r.idempotency_key));
      const after = reqs.filter((r) => r.incarnation > 1).map((r) => r.idempotency_key);
      ctx.check("同じ Idempotency-Key で再投入", after.some((k) => before.has(k)), `${before.size} / ${after.length}`);
      checkWrites(ctx);
    },
  },
  {
    id: "FI-11",
    fi: "FI-11",
    title: "アプリの異常終了（推論待ち）→ 再起動して再開",
    mock: "write.yaml",
    timeoutMs: 120_000,
    async run(ctx) {
      const h1 = ctx.host({}, "infer.submitted:2");
      await waitUntil(() => h1.frozen.length > 0, 60_000, "frozen");
      await h1.kill();
      const h2 = ctx.host({ mode: "resume" });
      await h2.finish(this.timeoutMs);
      checkWrites(ctx);
    },
  },
  {
    id: "FI-12",
    fi: "FI-12",
    title: "アプリの異常終了（書き込み中: prepared の直後）",
    mock: "write.yaml",
    timeoutMs: 120_000,
    async run(ctx) {
      const h1 = ctx.host({}, "intent.prepared:1");
      await waitUntil(() => h1.frozen.length > 0, 60_000, "frozen");
      await h1.kill();
      ctx.check("止めた時点では B-OUT は元のまま", read(join(ctx.ws, "out", "B-OUT.txt")) === "OLD");
      const h2 = ctx.host({ mode: "resume" });
      await h2.finish(this.timeoutMs);
      checkWrites(ctx);
    },
  },
  {
    id: "FI-13",
    fi: "FI-13",
    title: "アプリの異常終了（書き込み直後: 置き換えの後・committed の記録の前）",
    mock: "write.yaml",
    timeoutMs: 120_000,
    async run(ctx) {
      const h1 = ctx.host({}, "intent.replaced:1");
      await waitUntil(() => h1.frozen.length > 0, 60_000, "frozen");
      await h1.kill();
      ctx.check("止めた時点で B-OUT は置き換え済み", read(join(ctx.ws, "out", "B-OUT.txt")) === "A");
      const h2 = ctx.host({ mode: "resume" });
      await h2.finish(this.timeoutMs);
      checkWrites(ctx);
    },
  },
  {
    id: "FI-15",
    fi: "FI-15",
    title: "ファイルのロック（解放される）→ 待って元のパスに書く",
    mock: "write.yaml",
    timeoutMs: 120_000,
    async run(ctx) {
      // prepared の直後に 4 秒止めている間に、出力先を 8 秒ロックする
      const h = ctx.host({ lockWaitMs: 30_000 }, "intent.prepared:1:4000");
      await waitUntil(() => h.frozen.length > 0, 60_000, "frozen");
      const lock = await lockFile(join(ctx.ws, "out", "B-OUT.txt"), 8);
      await h.finish(this.timeoutMs);
      await lock.released;
      checkWrites(ctx);
      ctx.check("別名のファイルを作っていない", readdirSync(join(ctx.ws, "out")).sort().join(",") === "B-OUT.txt,C-OUT.txt", readdirSync(join(ctx.ws, "out")).join(","));
    },
  },
  {
    id: "FI-16",
    fi: "FI-16",
    title: "ファイルのロック（解放されない）→ 別名で保存し、warnings に残す（R12）",
    mock: "write.yaml",
    timeoutMs: 120_000,
    async run(ctx) {
      const h = ctx.host({ lockWaitMs: 4_000 }, "intent.prepared:1:4000");
      await waitUntil(() => h.frozen.length > 0, 60_000, "frozen");
      const lock = await lockFile(join(ctx.ws, "out", "B-OUT.txt"), 30);
      await h.finish(this.timeoutMs);
      await killTree(lock.proc.pid); // 確認のため読めるようにする
      await lock.released;
      ctx.check("元のファイルは変わらない", read(join(ctx.ws, "out", "B-OUT.txt")) === "OLD");
      const alt = readdirSync(join(ctx.ws, "out")).filter((n) => n.startsWith("B-OUT") && n !== "B-OUT.txt");
      ctx.check("別名で保存した", alt.length === 1 && read(join(ctx.ws, "out", alt[0]!)) === "A", alt.join(","));
      ctx.check("C-OUT は元のパス", read(join(ctx.ws, "out", "C-OUT.txt")) === "A");
      const store = RunStore.open(join(ctx.appData, "runs.db"), "fi");
      try {
        const rows = store.db.prepare("SELECT result FROM steps WHERE tool = 'fs.copy'").all() as { result: string | null }[];
        ctx.check("warnings に別名保存の記録", rows.some((r) => (r.result ?? "").includes("別名で保存")));
      } finally {
        store.close();
      }
    },
  },
  {
    id: "FI-19",
    fi: "FI-19",
    title: "承認待ち（2件）→ その間も他の手順が進み、承認後に完了",
    mock: "approval.yaml",
    timeoutMs: 90_000,
    async run(ctx) {
      const h = ctx.host({ approveAfterMs: 4_000 });
      await waitUntil(() => h.events.some((e) => e.type === "approval.request"), 60_000, "approval");
      await waitUntil(() => existsSync(join(ctx.ws, "progress")), 30_000, "承認待ちの間に進む");
      ctx.check("承認前はフォルダ外に書いていない", !existsSync(join(ctx.outside, "x1.txt")) && !existsSync(join(ctx.outside, "x2.txt")));
      await h.finish(this.timeoutMs);
      ctx.check("承認の通知が出た", h.events.some((e) => e.type === "notify" && (e.payload as { kind: string }).kind === "approval"));
      ctx.check("承認後に2件とも書いた", read(join(ctx.outside, "x1.txt")) === "A" && read(join(ctx.outside, "x2.txt")) === "A");
    },
  },
];

// ------------------------------------------------------------------ 実行と判定（試験仕様 §4「共通の判定方法」）
interface RunResult {
  completed: boolean;
  duplicates: number;
  end_reason: string | null;
  duration_s: number;
  checks: Check[];
  error?: string;
}

async function runOnce(sc: Scenario): Promise<RunResult> {
  const dir = mkdtempSync(join(tmpdir(), "lla-fi-"));
  const ws = join(dir, "ws");
  const outside = join(dir, "outside");
  const appData = join(dir, "appdata");
  for (const d of [ws, outside, appData, join(ws, "out")]) mkdirSync(d, { recursive: true });
  writeFileSync(join(ws, "a.txt"), "A");
  writeFileSync(join(ws, "out", "B-OUT.txt"), "OLD"); // 上書き（バックアップあり）の経路を通す
  const mock = new MockGatewayProc(join(SCENARIOS, sc.mock), join(dir, "mock"), { pingIntervalS: 0.3 });
  const checks: Check[] = [];
  const hosts: Host[] = [];
  const ctx: Ctx = {
    dir,
    ws,
    outside,
    appData,
    mock,
    hosts,
    checks,
    cfg: (over = {}) => ({ gatewayUrl: mock.url, appDataDir: appData, mode: "start", workspace: ws, request: REQUEST, ...over }),
    host: (over = {}, freeze) => {
      const h = new Host(ctx.cfg(over), freeze);
      hosts.push(h);
      return h;
    },
    check: (name, ok, detail) => checks.push({ name, ok, ...(detail !== undefined ? { detail } : {}) }),
  };
  const t0 = Date.now();
  let error: string | undefined;
  try {
    await mock.start();
    await sc.run(ctx);
  } catch (e) {
    error = (e as Error).message;
    for (const h of hosts) process.stderr.write(h.stderr.slice(-20).map((l) => `    | ${l}\n`).join(""));
  } finally {
    for (const h of hosts) await h.kill().catch(() => undefined);
    await mock.stop().catch(() => undefined);
  }
  // Run Store を走査: 完了したか、同じ (path, op_digest) が2回 committed になっていないか
  let endReason: string | null = null;
  let duplicates = 0;
  let open = 0;
  try {
    const store = RunStore.open(join(appData, "runs.db"), "fi");
    try {
      const run = store.db.prepare("SELECT end_reason FROM runs ORDER BY started_at DESC LIMIT 1").get() as { end_reason: string | null } | undefined;
      endReason = run?.end_reason ?? null;
      const dup = store.db
        .prepare("SELECT path, op_digest, COUNT(*) n FROM intents WHERE status = 'committed' GROUP BY path, op_digest HAVING n > 1")
        .all() as { n: number }[];
      duplicates = dup.reduce((a, r) => a + r.n - 1, 0);
      open = (store.db.prepare("SELECT COUNT(*) n FROM intents WHERE status IN ('prepared','staged')").get() as { n: number }).n;
    } finally {
      store.close();
    }
  } catch (e) {
    error ??= `Run Store を読めません: ${(e as Error).message}`;
  }
  checks.push({ name: "未確定の書き込みが残っていない", ok: open === 0, detail: String(open) });
  const completed = !error && endReason === "final" && checks.every((c) => c.ok);
  if (!completed && process.env.FI_DEBUG === "1") {
    for (const h of hosts) for (const e of h.events) process.stderr.write(`    > ${e.type} ${JSON.stringify(e.payload).slice(0, 200)}\n`);
  }
  rmSync(dir, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  return { completed, duplicates, end_reason: endReason, duration_s: (Date.now() - t0) / 1000, checks, ...(error ? { error } : {}) };
}

function parse(argv: string[]) {
  const o = { only: [] as string[], repeat: 1, out: join(FI_DIR, "results", "latest.json") };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]!;
    if (a === "--") continue;
    else if (a === "--only") o.only = argv[++i]!.split(",").map((s) => s.trim());
    else if (a === "--repeat") o.repeat = Number(argv[++i]);
    else if (a === "--out") o.out = resolve(argv[++i]!);
    else throw new Error(`不明な引数: ${a}`);
  }
  return o;
}

async function main(): Promise<number> {
  const o = parse(process.argv.slice(2));
  const list = SCENARIO_LIST.filter((s) => !o.only.length || o.only.includes(s.id));
  const startedAt = new Date().toISOString();
  const scenarios = [];
  let failed = 0;
  for (const sc of list) {
    const runs: RunResult[] = [];
    for (let i = 0; i < o.repeat; i++) {
      const r = await runOnce(sc);
      runs.push(r);
      const mark = r.completed && r.duplicates === 0 ? "合格" : "不合格";
      process.stderr.write(`${sc.id} #${i + 1} ${mark}（${r.duration_s.toFixed(1)}秒、end=${r.end_reason}、重複 ${r.duplicates}）${sc.title}\n`);
      for (const c of r.checks.filter((c) => !c.ok)) process.stderr.write(`    ✗ ${c.name}${c.detail ? `: ${c.detail}` : ""}\n`);
      if (r.error) process.stderr.write(`    ✗ ${r.error}\n`);
    }
    if (!runs.every((r) => r.completed && r.duplicates === 0)) failed++;
    scenarios.push({ id: sc.id, fi: sc.fi, title: sc.title, runs });
  }
  const report = { run_id: `fi-${startedAt.replace(/[-:]/g, "").replace(/\..+$/, "")}`, started_at: startedAt, repeat: o.repeat, scenarios };
  mkdirSync(dirname(o.out), { recursive: true });
  writeFileSync(o.out, JSON.stringify(report, null, 1));
  process.stderr.write(`\n${list.length - failed}/${list.length} シナリオ合格 → ${o.out}\n`);
  return failed ? 1 : 0;
}

main().then(
  (code) => process.exit(code),
  (e: unknown) => {
    process.stderr.write(`${(e as Error).stack ?? String(e)}\n`);
    process.exit(2);
  },
);
