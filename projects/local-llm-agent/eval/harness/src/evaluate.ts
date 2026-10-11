// 評価ハーネス（T-50、local_llm_test_spec.md §3、design §8.5）。
// 評価セットを検証 → Runtime をヘッドレスで動かして成果物を集める → Python 側で採点 → 結果 JSON と合否表。
// 接続先は --gateway で切り替える（省略時は、セットに書かれたモック Gateway のシナリオを起動する）。
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, isAbsolute, join, relative, resolve } from "node:path";
import type { RuntimeEvent } from "@lla/protocol";
import { HARNESS_DIR, SETS_DIR, llaEval, sleep } from "./env.js";
import { createHeadless } from "./headless.js";
import { MockGatewayProc } from "./mock.js";

export interface MetricRow {
  name: string;
  target: string;
  value: number | null;
  verdict: string;
}

interface Manifest {
  name: string;
  version: number;
  runner: string;
  scorer: "invoice_fields" | "artifact_checks" | "doc_quality" | "resilience";
  timeout_per_item_s: number;
  repeat: number;
  temperature: number;
  mock: { scenario?: string };
}

export interface LoadedSet {
  set_dir: string;
  manifest: Manifest;
  items: Record<string, unknown>[];
  data_root: string;
  data_hash: string;
  split_check: { status: string };
}

export interface EvalOptions {
  /** 評価セットのフォルダ、または eval/sets の下の名前 */
  set: string;
  /** 接続先の Gateway（本物・検証機）。省略するとセットのモック Gateway を起動する */
  gateway?: string;
  login?: string;
  password?: string;
  /** モデル台帳の ID（結果に記録する） */
  modelId?: string;
  /** run.start のモデル指定 */
  model?: "auto" | "fast" | "accurate";
  /** 結果の出力先（既定 eval/harness/results/<set>） */
  out?: string;
  /** 合成データの置き場（既定は OS の一時フォルダ） */
  cache?: string;
  /** 継続実行セット: 障害注入の結果 JSON（既定 eval/fault-injection/results/latest.json） */
  fiReport?: string;
  keep?: boolean;
  /** 結果をモデル台帳（POST /admin/v1/models/{modelId}/evals）に登録する。--gateway と --model-id が必要 */
  register?: boolean;
  /** 台帳への登録に使う管理者（既定は環境変数 LLA_EVAL_ADMIN_LOGIN / LLA_EVAL_ADMIN_PASSWORD） */
  adminLogin?: string;
  adminPassword?: string;
  log?: (s: string) => void;
}

export interface EvalRun {
  runId: string;
  resultPath: string;
  verdict: string;
  metrics: MetricRow[];
}

type Outputs = Record<string, unknown>;

let cmdSeq = 0;
const cmd = (type: string, payload: Record<string, unknown>) => ({ v: 1 as const, id: `h${++cmdSeq}`, type: type as never, payload });

export function resolveSetDir(set: string): string {
  if (existsSync(join(set, "manifest.yaml"))) return resolve(set);
  const p = join(SETS_DIR, set);
  if (existsSync(join(p, "manifest.yaml"))) return p;
  throw new Error(`評価セットが見つかりません: ${set}`);
}

export async function loadSet(setDir: string, cache: string): Promise<LoadedSet> {
  return llaEval<LoadedSet>(["load", setDir, "--cache", cache]);
}

function listFiles(dir: string): string[] {
  if (!existsSync(dir)) return [];
  const out: string[] = [];
  const walk = (d: string) => {
    for (const n of readdirSync(d)) {
      const p = join(d, n);
      if (statSync(p).isDirectory()) walk(p);
      else out.push(relative(dir, p));
    }
  };
  walk(dir);
  return out;
}

// ------------------------------------------------------------------ runner: tool（帳票抽出: invoice.extract を直接呼ぶ）
async function runInvoice(loaded: LoadedSet, gatewayUrl: string, cred: { login: string; password: string }, work: string): Promise<Outputs> {
  const outputs: Outputs = {};
  const h = createHeadless({ gatewayUrl, ...cred, appDataDir: work, dbKey: "eval", office: true, emit: () => {} });
  try {
    for (const it of loaded.items) {
      const id = String(it.id);
      const t0 = Date.now();
      const r = await h.router.call("invoice.extract", { path: join(loaded.data_root, String(it.file)), schema: String(it.schema) }, {
        workspace: loaded.data_root,
        conversationId: "eval",
        runId: "eval",
        stepId: `eval:${id}`,
      });
      outputs[id] = { ok: r.ok, data: r.data ?? null, error: r.error ?? null, duration_s: (Date.now() - t0) / 1000 };
    }
  } finally {
    await h.close();
  }
  return outputs;
}

// ------------------------------------------------------------------ runner: tool（文書品質: ツールを直接呼んで編集する）
async function runDocOps(loaded: LoadedSet, work: string): Promise<Outputs> {
  const outputs: Outputs = {};
  // 文書の編集は推論を使わない。Gateway には接続しない（使われない URL を渡す）
  const h = createHeadless({ gatewayUrl: "http://127.0.0.1:9", login: "-", password: "-", appDataDir: work, dbKey: "eval", office: true, emit: () => {} });
  try {
    for (const it of loaded.items) {
      const id = String(it.id);
      const dir = join(work, "docs", id);
      mkdirSync(dir, { recursive: true });
      let path: string;
      if (it.source) {
        path = join(dir, basename(String(it.source)));
        cpSync(join(loaded.data_root, String(it.source)), path);
      } else path = join(dir, String(it.output));
      let ok = true;
      let error: unknown = null;
      for (const op of it.ops as { tool: string; args: Record<string, unknown> }[]) {
        const r = await h.router.call(op.tool, { ...op.args, path }, { workspace: dir, conversationId: "eval", runId: "eval", stepId: `eval:${id}` });
        if (!r.ok) {
          ok = false;
          error = r.error;
          break;
        }
      }
      outputs[id] = { ok, output: path, error };
    }
  } finally {
    await h.close();
  }
  return outputs;
}

// ------------------------------------------------------------------ runner: agent（ツール操作: Runtime をヘッドレスで動かす）
async function runAgentItem(
  loaded: LoadedSet,
  it: Record<string, unknown>,
  gatewayUrl: string,
  cred: { login: string; password: string },
  work: string,
  model: string,
): Promise<Record<string, unknown>> {
  const id = String(it.id);
  const dir = join(work, "agent", id);
  const ws = join(dir, "ws");
  const outside = join(dir, "outside");
  const appData = join(dir, "appdata");
  for (const d of [ws, outside, appData]) mkdirSync(d, { recursive: true });
  if (it.workspace_fixture) cpSync(join(loaded.data_root, String(it.workspace_fixture)), ws, { recursive: true });
  const events: RuntimeEvent[] = [];
  const h = createHeadless({ gatewayUrl, ...cred, appDataDir: appData, dbKey: "eval", office: true, emit: (e) => events.push(e), runtime: { retryBaseMs: 500 } });
  const t0 = Date.now();
  try {
    await h.runtime.start();
    if (it.net_level) await h.runtime.handle(cmd("settings.update", { net_level: it.net_level }));
    const { run_id } = (await h.runtime.handle(
      cmd("run.start", { conversation_id: `eval-${id}`, text: String(it.request), attachments: [], workspace: ws, model }),
    )) as { run_id: string };
    const deadline = t0 + loaded.manifest.timeout_per_item_s * 1000;
    let timedOut = false;
    while (!h.store.getRun(run_id)?.ended_at) {
      if (Date.now() > deadline) {
        timedOut = true;
        await h.runtime.handle(cmd("run.stop", { run_id }));
        break;
      }
      await sleep(100);
    }
    const run = h.store.getRun(run_id)!;
    const steps = (h.store.db.prepare("SELECT COUNT(*) n FROM steps WHERE run_id = ?").get(run_id) as { n: number }).n;
    return {
      end_reason: timedOut ? "timeout" : run.end_reason,
      steps,
      duration_s: (Date.now() - t0) / 1000,
      workspace: ws,
      outside_files: listFiles(outside),
      final: run.final_action ? (JSON.parse(run.final_action) as unknown) : null,
      retries: events.filter((e) => e.type === "retry").length,
    };
  } finally {
    await h.close();
  }
}

async function runAgentTasks(
  loaded: LoadedSet,
  o: EvalOptions,
  cred: { login: string; password: string },
  work: string,
  log: (s: string) => void,
): Promise<Outputs> {
  const outputs: Outputs = {};
  const model = o.model ?? "auto";
  for (const it of loaded.items) {
    const id = String(it.id);
    let mock: MockGatewayProc | null = null;
    let url = o.gateway;
    if (!url) {
      if (!it.mock) throw new Error(`${id}: --gateway の指定がなく、モックのシナリオ（mock）もありません`);
      mock = new MockGatewayProc(join(loaded.set_dir, String(it.mock)), join(work, "mock", id), { pingIntervalS: 0.5 });
      await mock.start();
      url = mock.url;
    }
    try {
      outputs[id] = await runAgentItem(loaded, it, url, cred, work, model);
      log(`  ${id}: ${(outputs[id] as { end_reason: string }).end_reason}`);
    } finally {
      await mock?.stop();
    }
  }
  return outputs;
}

// ------------------------------------------------------------------ モデル台帳への登録（設計書 §8.4）
export async function registerResults(o: EvalOptions, rows: unknown[]): Promise<number> {
  if (!o.gateway || !o.modelId) throw new Error("--register には --gateway と --model-id が必要です");
  if (rows.length === 0) return 0;
  const login = o.adminLogin ?? process.env.LLA_EVAL_ADMIN_LOGIN;
  const password = o.adminPassword ?? process.env.LLA_EVAL_ADMIN_PASSWORD;
  if (!login || !password) throw new Error("台帳への登録には管理者の資格情報が必要です（LLA_EVAL_ADMIN_LOGIN / LLA_EVAL_ADMIN_PASSWORD）");
  const auth = await fetch(`${o.gateway}/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ login, password, device_name: "eval-harness" }),
  });
  if (!auth.ok) throw new Error(`管理者でログインできません: HTTP ${auth.status}`);
  const token = ((await auth.json()) as { access_token: string }).access_token;
  const r = await fetch(`${o.gateway}/admin/v1/models/${encodeURIComponent(o.modelId)}/evals`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ results: rows }),
  });
  if (!r.ok) throw new Error(`台帳に登録できません: HTTP ${r.status} ${await r.text()}`);
  return ((await r.json()) as { added: number }).added;
}

// ------------------------------------------------------------------ 本体
export async function runEvaluation(o: EvalOptions): Promise<EvalRun[]> {
  const log = o.log ?? ((s: string) => process.stderr.write(`${s}\n`));
  const setDir = resolveSetDir(o.set);
  const cache = o.cache ?? join(tmpdir(), "lla-eval-cache");
  const loaded = await loadSet(setDir, cache);
  const m = loaded.manifest;
  if (loaded.split_check.status !== "ok") log(`警告: 学習データとの重複照合を行っていません（${loaded.split_check.status}）`);
  const out = o.out ?? join(HARNESS_DIR, "results", m.name);
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\..+$/, "").replace("T", "-");
  const baseId = `${m.name}-${stamp}`;
  const runs: EvalRun[] = [];

  for (let r = 1; r <= Math.max(1, m.repeat); r++) {
    const runId = m.repeat > 1 ? `${baseId}-r${r}` : baseId;
    const work = mkdtempSync(join(tmpdir(), "lla-eval-"));
    const notes: string[] = [];
    let gatewayUrl = o.gateway ?? "";
    let gatewayKind = o.gateway ? "external" : "mock";
    const cred = o.gateway
      ? { login: o.login ?? process.env.LLA_EVAL_LOGIN ?? "dev", password: o.password ?? process.env.LLA_EVAL_PASSWORD ?? "dev" }
      : { login: "dev", password: "dev" };
    let setMock: MockGatewayProc | null = null;
    const t0 = Date.now();
    const startedAt = new Date().toISOString();
    try {
      log(`[${m.name}] run ${runId}（runner=${m.runner}, scorer=${m.scorer}, items=${loaded.items.length}）`);
      let outputs: Outputs;
      if (m.scorer === "invoice_fields") {
        if (!gatewayUrl) {
          if (!m.mock.scenario) throw new Error("--gateway の指定がなく、モックのシナリオ（mock.scenario）もありません");
          setMock = new MockGatewayProc(join(setDir, m.mock.scenario), join(work, "mock"), { pingIntervalS: 0.5 });
          await setMock.start();
          gatewayUrl = setMock.url;
        }
        outputs = await runInvoice(loaded, gatewayUrl, cred, work);
      } else if (m.scorer === "artifact_checks") {
        outputs = await runAgentTasks(loaded, o, cred, work, log);
        if (!o.gateway) gatewayUrl = "（item ごとのモック）";
      } else if (m.scorer === "doc_quality") {
        outputs = await runDocOps(loaded, work);
        gatewayKind = "none";
        gatewayUrl = "（推論なし）";
      } else {
        // 障害注入（resilience）は latest.json、セキュリティ（security）など他のセットは <セット名>.json
        const report = o.fiReport ?? join(HARNESS_DIR, "..", "fault-injection", "results", m.name === "resilience" ? "latest.json" : `${m.name}.json`);
        if (!existsSync(report)) throw new Error(`障害注入の結果がありません: ${report}（eval/fault-injection を先に実行してください）`);
        outputs = JSON.parse(readFileSync(report, "utf8")) as Outputs;
        gatewayKind = "fault-injection";
        gatewayUrl = isAbsolute(report) ? report : resolve(report);
        notes.push("継続実行の値は eval/fault-injection の結果から集計した");
      }
      const record = {
        run_id: runId,
        model_id: o.modelId ?? (o.gateway ? "unknown" : "mock"),
        gateway: gatewayUrl,
        gateway_kind: gatewayKind,
        started_at: startedAt,
        duration_s: (Date.now() - t0) / 1000,
        config: { model: o.model ?? "auto", repeat_index: r },
        outputs,
        notes,
      };
      const recordPath = join(work, "record.json");
      writeFileSync(recordPath, JSON.stringify(record));
      const fin = await llaEval<{ result: string; verdict: string; metrics: MetricRow[] }>(
        ["finalize", setDir, "--record", recordPath, "--out", out, "--cache", cache],
        { echoStderr: true },
      );
      runs.push({ runId, resultPath: fin.result, verdict: fin.verdict, metrics: fin.metrics });
      if (o.register) {
        const rows = JSON.parse(readFileSync(join(out, `${runId}.registry.json`), "utf8")) as unknown[];
        const added = await registerResults(o, rows);
        log(`  モデル台帳に登録しました: ${o.modelId}（${added} 件）`);
      }
    } finally {
      await setMock?.stop();
      if (!o.keep) rmSync(work, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
    }
  }
  if (runs.length > 1) {
    const names = runs[0]!.metrics.map((x) => x.name);
    const summary = names.map((name) => {
      const vals = runs.map((r) => r.metrics.find((x) => x.name === name)?.value).filter((v): v is number => typeof v === "number");
      return { name, mean: vals.reduce((a, b) => a + b, 0) / (vals.length || 1), min: Math.min(...vals), max: Math.max(...vals) };
    });
    writeFileSync(join(out, `${baseId}.summary.json`), JSON.stringify({ runs: runs.map((r) => r.runId), summary }, null, 1));
    for (const s of summary) log(`  ${s.name}: 平均 ${s.mean.toFixed(4)} 最小 ${s.min} 最大 ${s.max}`);
  }
  return runs;
}
