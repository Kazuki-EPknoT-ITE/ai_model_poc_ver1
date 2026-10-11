// 障害注入試験（T-51、試験仕様 §4 FI-01〜20）のランナー。CI（開発PC）で回せるものを自動化している。
//   pnpm fi [--only FI-11,FI-12] [--repeat 3] [--out <結果 JSON>]
// 本物のプロセスを使う: Runtime は子プロセス（host.ts）、推論はモック Gateway（Python）。
// 注入: 子プロセスの強制終了（taskkill /F /T）、モック Gateway の停止・再起動、ファイルのロック（tools/lockfile.ps1）。
// 結果は eval/fault-injection/results/latest.json（評価ハーネスの scorer: resilience が読む）。
import { cpSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync, existsSync } from "node:fs";
import type { ChildProcess } from "node:child_process";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { RuntimeEvent } from "@lla/protocol";
import { RunStore } from "@lla/agent-runtime";
import { PROJECT, exec, freePort, killTree, sleep, spawnLines, uvPython, viteNodeBin, waitHttp, waitUntil } from "../../harness/src/env.js";
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

  constructor(cfg: HostConfig, freeze?: string, extraEnv: Record<string, string> = {}) {
    const env: NodeJS.ProcessEnv = { ...process.env, ...extraEnv, AGENT_TEST_HOOKS: "1", FI_HOST_CONFIG: JSON.stringify(cfg) };
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
  host(over?: Partial<HostConfig>, freeze?: string, env?: Record<string, string>): Host;
  checks: Check[];
  check(name: string, ok: boolean, detail?: string): void;
}

interface Scenario {
  id: string;
  fi: string;
  title: string;
  /** モック Gateway のシナリオ。null なら起動しない（本物の Gateway を使うシナリオ） */
  mock: string | null;
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

/** その Run Store の最後の Run の終わり方。 */
function runEnd(appData: string): string | null {
  const store = RunStore.open(join(appData, "runs.db"), "fi");
  try {
    return (store.db.prepare("SELECT end_reason FROM runs ORDER BY started_at DESC LIMIT 1").get() as { end_reason: string | null } | undefined)?.end_reason ?? null;
  } finally {
    store.close();
  }
}

/** 本物の Gateway（server/gateway/scripts/dev_server.py。FakeEngine・SQLite・永続キュー）。 */
async function startRealGateway(port: number, dir: string): Promise<ChildProcess> {
  const args = ["run", "--project", PROJECT, "python", "server/gateway/scripts/dev_server.py", "--port", String(port), "--dir", dir, "--fake-delay", "0.3", "--max-concurrent", "1"];
  const proc = spawnLines("uv", args, {});
  await waitHttp(`http://127.0.0.1:${port}/healthz`, 120_000);
  return proc;
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
    id: "FI-01",
    fi: "FI-01",
    title: "手順数・時間の上限（300手。時計を400倍に早送りして2時間超を模擬）",
    mock: null,
    timeoutMs: 300_000,
    async run(ctx) {
      // ファイル150件を1件ずつ「読む → コピーする」= 300手。モックの手順は試験のたびに作る
      const steps: unknown[] = [];
      for (let i = 0; i < 150; i++) {
        const name = `f${String(i).padStart(3, "0")}.txt`;
        writeFileSync(join(ctx.ws, name), `ファイル${i} の本文`);
        steps.push({ match: { step_index: 2 * i }, latency_ms: 0, action: { type: "tool_call", tool: "fs.read_text", args: { path: name } } });
        steps.push({ match: { step_index: 2 * i + 1 }, latency_ms: 0, action: { type: "tool_call", tool: "fs.copy", args: { src: name, dst: `out/${name}` } } });
      }
      steps.push({ match: { purpose: "agent" }, repeat: true, action: { type: "final", summary: "150件を処理しました。", outputs: [], failures: [], needs_review: [] } });
      const scenario = join(ctx.dir, "long.yaml"); // JSON は YAML として読める
      writeFileSync(scenario, JSON.stringify({ name: "fi-long", config: { context_budget: 32768, models: ["local-12b"] }, steps }));
      const mock = new MockGatewayProc(scenario, join(ctx.dir, "mock2"), { pingIntervalS: 0.3 });
      await mock.start();
      try {
        const h = ctx.host({ gatewayUrl: mock.url }, undefined, { AGENT_TEST_CLOCK_SPEED: "400" });
        await h.finish(this.timeoutMs);
        const notices = h.events.filter((e) => e.type === "notify" && (e.payload as { kind: string }).kind === "long_run");
        ctx.check("long_run の通知がちょうど1回", notices.length === 1, String(notices.length));
        // 実行の途中（最初と最後の手順の間）で通知している
        const at = h.events.indexOf(notices[0]!);
        const stepIdx = h.events.map((e, i) => (e.type === "step" ? i : -1)).filter((i) => i >= 0);
        ctx.check("通知は実行の途中", at > stepIdx[0]! && at < stepIdx[stepIdx.length - 1]!);
      } finally {
        await mock.stop();
      }
      ctx.check("150件ともコピーした", readdirSync(join(ctx.ws, "out")).filter((n) => /^f\d{3}\.txt$/.test(n)).length === 150);
      const store = RunStore.open(join(ctx.appData, "runs.db"), "fi");
      try {
        const n = (store.db.prepare("SELECT COUNT(*) n FROM steps").get() as { n: number }).n;
        ctx.check("手順数の上限で止まらない（300手を超えて完了）", n >= 301, String(n));
      } finally {
        store.close();
      }
    },
  },
  {
    id: "FI-02",
    fi: "FI-02",
    title: "文脈の満杯（予算 8K で長い契約書3本）→ 圧縮して固定メモリを保ったまま完了",
    mock: "compact.yaml",
    timeoutMs: 90_000,
    async run(ctx) {
      for (const name of ["A", "B", "C"]) {
        const body = Array.from({ length: 150 }, (_, i) => `契約${name} 第${i + 1}条 ${"本契約の条項。".repeat(3)}`).join("\n");
        writeFileSync(join(ctx.ws, `契約${name}.txt`), body);
      }
      const h = ctx.host();
      await h.finish(this.timeoutMs);
      ctx.check("圧縮が起きた（compacting）", h.states().includes("compacting"));
      const store = RunStore.open(join(ctx.appData, "runs.db"), "fi");
      try {
        ctx.check("固定メモリが保たれた", JSON.stringify(store.listMemory("fi")) === JSON.stringify([{ key: "出力先", value: "出力/比較表.xlsx" }]), JSON.stringify(store.listMemory("fi")));
        const summarized = (store.db.prepare("SELECT COUNT(*) n FROM messages WHERE conversation_id = 'fi' AND compacted_into IS NOT NULL").get() as { n: number }).n;
        ctx.check("古い発言が要約に置き換わった", summarized > 0, String(summarized));
      } finally {
        store.close();
      }
    },
  },
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
    id: "FI-04",
    fi: "FI-04",
    title: "迷走（本番構成）→ 立て直し → 26B に昇格して完了",
    mock: "escalate.yaml",
    timeoutMs: 90_000,
    async run(ctx) {
      const h = ctx.host();
      await h.finish(this.timeoutMs);
      const st = h.events.filter((e) => e.type === "run.state").map((e) => e.payload as { state: string; detail?: { escalated_to?: string } });
      ctx.check("立て直し → 昇格の順", st.some((x) => x.state === "replanning" && !x.detail?.escalated_to) && st.some((x) => x.detail?.escalated_to === "local-26b"));
      ctx.check("stalled にならない", !st.some((x) => x.state === "stalled"));
      const store = RunStore.open(join(ctx.appData, "runs.db"), "fi");
      try {
        const run = store.db.prepare("SELECT model_current, escalated_at FROM runs ORDER BY started_at DESC LIMIT 1").get() as { model_current: string; escalated_at: string | null };
        ctx.check("昇格の記録が残る", run.model_current === "local-26b" && run.escalated_at !== null, JSON.stringify(run));
      } finally {
        store.close();
      }
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
    id: "FI-07",
    fi: "FI-07",
    title: "サーバーの再起動（本物の Gateway。3件が待ち行列にある状態で再起動）",
    mock: null,
    timeoutMs: 180_000,
    async run(ctx) {
      const port = await freePort();
      const url = `http://127.0.0.1:${port}`;
      const gwDir = join(ctx.dir, "gw");
      let gw = await startRealGateway(port, gwDir);
      try {
        // 3人分（別々の Run Store）を同時に始める。Gateway の同時実行は1なので、2件は待ち行列に入る
        const hosts = [0, 1, 2].map((i) => {
          const ws = i === 0 ? ctx.ws : join(ctx.dir, `ws${i}`);
          const appData = i === 0 ? ctx.appData : join(ctx.dir, `appdata${i}`);
          mkdirSync(ws, { recursive: true });
          mkdirSync(appData, { recursive: true });
          return ctx.host({ gatewayUrl: url, login: "dev", password: "dev-password-1", workspace: ws, appDataDir: appData });
        });
        await waitUntil(() => hosts.every((h) => h.hooks.some((x) => x.point === "infer.submitted")), 120_000, "3 jobs submitted");
        await sleep(500);
        const restartAt = Date.now();
        await killTree(gw.pid);
        await sleep(2_000);
        gw = await startRealGateway(port, gwDir);
        for (const h of hosts) await h.finish(this.timeoutMs);
        ctx.check("3件とも完了", hosts.every((_, i) => runEnd(i === 0 ? ctx.appData : join(ctx.dir, `appdata${i}`)) === "final"));
        // Gateway の DB: ジョブは3件だけ（同じ手を二重に受け付けていない）、投入の順に終わっている
        const py = "import sqlite3,json,sys; c=sqlite3.connect(sys.argv[1]); print(json.dumps(c.execute('select state, enqueued_at, finished_at, requeue_count from jobs order by enqueued_at').fetchall()))";
        const r = await exec("uv", ["run", "--project", PROJECT, "python", "-c", py, join(gwDir, "gw.db")]);
        const jobs = JSON.parse(r.stdout.trim().split(/\r?\n/).pop() ?? "[]") as [string, string, string | null, number][];
        ctx.check("ジョブは3件", jobs.length === 3, JSON.stringify(jobs.map((j) => j[0])));
        const finished = jobs.map((j) => j[2] ?? "");
        ctx.check("投入の順に終わった", finished.every((f, i) => i === 0 || f >= finished[i - 1]!), JSON.stringify(finished));
        // 日時は UTC（SQLite には時差なしで入る）
        const ms = (t: string | null) => (t ? Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(t) ? t.replace(" ", "T") : `${t.replace(" ", "T")}Z`) : NaN);
        ctx.check("3件とも再起動の前に受け付けた", jobs.every((j) => ms(j[1]) < restartAt), JSON.stringify(jobs.map((j) => j[1])));
        ctx.check("待ち行列のジョブが再起動の後に終わった（キューの復元）", jobs.filter((j) => ms(j[2]) > restartAt).length >= 2, JSON.stringify(finished));
      } finally {
        await killTree(gw.pid);
      }
    },
  },
  {
    id: "FI-08",
    fi: "FI-08",
    title: "LAN の切断（推論待ちの間に推論サーバーへ20秒届かない。CI ではモックの停止で代替）",
    mock: "write.yaml",
    timeoutMs: 150_000,
    async run(ctx) {
      const h = ctx.host();
      await waitUntil(() => h.hooks.some((x) => x.point === "infer.submitted" && x.n === 2), 60_000, "2nd inference");
      await ctx.mock.stop();
      await sleep(20_000);
      const retriesDuringOutage = h.events.filter((e) => e.type === "retry").length;
      await ctx.mock.start();
      await h.finish(this.timeoutMs);
      ctx.check("切断中も再接続を試み続けた", retriesDuringOutage >= 3, String(retriesDuringOutage));
      checkWrites(ctx);
    },
  },
  {
    id: "FI-09",
    fi: "FI-09",
    title: "LAN の切断（書き込み中: prepared の直後に切断。CI ではモックの停止で代替）",
    mock: "write.yaml",
    timeoutMs: 150_000,
    async run(ctx) {
      // prepared の直後に 1.5 秒止め、その間に推論サーバーを止める。書き込みはローカルなので続く
      const h = ctx.host({}, "intent.prepared:1:1500");
      await waitUntil(() => h.frozen.length > 0, 60_000, "frozen");
      await ctx.mock.stop();
      await waitUntil(() => h.hooks.some((x) => x.point === "intent.replaced"), 30_000, "書き込みが切断中に終わる");
      ctx.check("切断中に B-OUT の書き込みが終わった", read(join(ctx.ws, "out", "B-OUT.txt")) === "A");
      await sleep(8_000);
      await ctx.mock.start();
      await h.finish(this.timeoutMs);
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
    id: "FI-17",
    fi: "FI-17",
    title: "他人による同時編集（読み取りの後・書き込みの前に文書を書き換える）→ STALE_DOCUMENT → 読み直して編集",
    mock: "stale.yaml",
    timeoutMs: 150_000,
    async run(ctx) {
      const doc = join(ctx.ws, "report.docx");
      cpSync(join(SCENARIOS, "fixtures", "report.docx"), doc);
      // 読み取りの結果を受けた推論（2回目）を投入した直後に 8 秒止め、その間に他人の編集を入れる
      const h = ctx.host({ office: true }, "infer.submitted:2:8000");
      await waitUntil(() => h.frozen.length > 0, 120_000, "frozen");
      const other = await uvPython([join(FI_DIR, "tools", "docx_fixture.py"), "append", doc, "他人の追記"]);
      ctx.check("他人の編集を模擬できた", other.code === 0, other.stderr.slice(-300));
      await h.finish(this.timeoutMs);
      const py = "import sys,json; from docx import Document; print(json.dumps([p.text for p in Document(sys.argv[1]).paragraphs], ensure_ascii=False))";
      const r = await uvPython(["-c", py, doc]);
      const paras = JSON.parse(r.stdout.trim().split(/\r?\n/).pop() ?? "[]") as string[];
      ctx.check("他人の変更が失われていない", paras.includes("他人の追記"), JSON.stringify(paras));
      ctx.check("AI の編集が入った", paras.includes("本文: AIが更新"), JSON.stringify(paras));
      const store = RunStore.open(join(ctx.appData, "runs.db"), "fi");
      try {
        const codes = (store.db.prepare("SELECT error_code FROM steps WHERE error_code IS NOT NULL").all() as { error_code: string }[]).map((x) => x.error_code);
        ctx.check("STALE_DOCUMENT を受けた", codes.includes("STALE_DOCUMENT"), JSON.stringify(codes));
      } finally {
        store.close();
      }
    },
  },
  {
    id: "FI-18",
    fi: "FI-18",
    title: "壊れたファイル（請求書20件のうち5件: ヘッダー破損・0バイト・暗号化）→ 15件を転記し、5件を理由付きで失敗に挙げる",
    mock: null,
    timeoutMs: 240_000,
    async run(ctx) {
      // 請求書と、抽出推論の決め打ち（モックのシナリオと同じフォルダ）を作る
      const mockDir = join(ctx.dir, "mockset");
      mkdirSync(mockDir, { recursive: true });
      cpSync(join(SCENARIOS, "batch.yaml"), join(mockDir, "batch.yaml"));
      const broken = JSON.stringify({ header: [4], empty: [9, 15], encrypted: [12, 18] });
      const gen = await uvPython(["-m", "lla_eval", "gen-invoices", join(ctx.ws, "請求書"), "--count", "20", "--broken", broken, "--no-expected", "--mock-fixtures", join(mockDir, "fixtures", "invoice")]);
      if (gen.code !== 0) throw new Error(`請求書を作れません: ${gen.stderr.slice(-300)}`);
      const mock = new MockGatewayProc(join(mockDir, "batch.yaml"), join(ctx.dir, "mock2"), { pingIntervalS: 0.3 });
      await mock.start();
      try {
        const h = ctx.host({ gatewayUrl: mock.url, office: true });
        await h.finish(this.timeoutMs);
      } finally {
        await mock.stop();
      }
      const py = "import json,sys,openpyxl; ws=openpyxl.load_workbook(sys.argv[1])['明細']; print(json.dumps([[c.value for c in r] for r in ws.iter_rows()], default=str, ensure_ascii=False))";
      const r = await uvPython(["-c", py, join(ctx.ws, "送金明細.xlsx")]);
      const rows = JSON.parse(r.stdout.trim().split(/\r?\n/).pop() ?? "[]") as unknown[][];
      ctx.check("15件を転記（見出し＋15行。二重転記なし）", rows.length === 16, `${rows.length} 行`);
      const store = RunStore.open(join(ctx.appData, "runs.db"), "fi");
      try {
        const row = store.db.prepare("SELECT result FROM steps WHERE tool = 'batch.run' AND result IS NOT NULL").get() as { result: string } | undefined;
        const text = row?.result ?? "";
        const res = JSON.parse(text || "{}") as { data?: { succeeded?: number; failed?: { item: string; code?: string; reason?: string }[] } };
        const failed = res.data?.failed ?? [];
        ctx.check("一括処理の結果: 成功15・失敗5", res.data?.succeeded === 15 && failed.length === 5, JSON.stringify({ s: res.data?.succeeded, f: failed.length }));
        ctx.check("失敗には理由（エラーコード）がある", failed.every((f) => Boolean(f.code && f.reason)), JSON.stringify(failed.map((f) => f.code)));
        const bad = ["INV-004", "INV-009", "INV-012", "INV-015", "INV-018"];
        ctx.check("壊れた5件が理由付きで失敗に挙がる", bad.every((b) => text.includes(b)), text.slice(0, 300));
        const fin = store.db.prepare("SELECT final_action FROM runs ORDER BY started_at DESC LIMIT 1").get() as { final_action: string | null };
        const failures = (JSON.parse(fin.final_action ?? "{}") as { failures?: unknown[] }).failures ?? [];
        ctx.check("完了報告の failures に5件", failures.length === 5);
      } finally {
        store.close();
      }
    },
  },
  {
    id: "FI-20",
    fi: "FI-20",
    title: "Web ページが開けない（最初の2つの URL が 404 とタイムアウト）→ 別の検索結果で代替して完了",
    mock: "web.yaml",
    timeoutMs: 90_000,
    async run(ctx) {
      const h = ctx.host({ netLevel: "browse" });
      await h.finish(this.timeoutMs);
      const store = RunStore.open(join(ctx.appData, "runs.db"), "fi");
      try {
        const fetches = store.db.prepare("SELECT error_code FROM steps WHERE tool = 'web.fetch' ORDER BY seq").all() as { error_code: string | null }[];
        ctx.check("3つの URL を順に試した（失敗2・成功1）", fetches.length === 3 && fetches.filter((f) => f.error_code).length === 2 && fetches[2]!.error_code === null, JSON.stringify(fetches));
      } finally {
        store.close();
      }
      ctx.check("取得の失敗を再試行し続けていない", !h.events.some((e) => e.type === "retry"));
      ctx.check("取得できなかった旨が完了報告に残る", (finalSummary(ctx) ?? "").includes("取得できませんでした"));
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
  const mock = new MockGatewayProc(join(SCENARIOS, sc.mock ?? "write.yaml"), join(dir, "mock"), { pingIntervalS: 0.3 });
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
    host: (over = {}, freeze, env) => {
      const h = new Host(ctx.cfg(over), freeze, env);
      hosts.push(h);
      return h;
    },
    check: (name, ok, detail) => checks.push({ name, ok, ...(detail !== undefined ? { detail } : {}) }),
  };
  const t0 = Date.now();
  let error: string | undefined;
  try {
    if (sc.mock) await mock.start();
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
