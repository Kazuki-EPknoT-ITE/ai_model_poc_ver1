// 手順例1件の実行と検証（gen.ts と試験から使う）
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import type { RuntimeEvent } from "@lla/protocol";
import { sleep, uvPython } from "../../../eval/harness/src/env.js";
import { createHeadless } from "../../../eval/harness/src/headless.js";
import { MockGatewayProc } from "../../../eval/harness/src/mock.js";
import type { Episode } from "./templates.js";

let seq = 0;
const cmd = (type: string, payload: Record<string, unknown>) => ({ v: 1 as const, id: `g${++seq}`, type: type as never, payload });

export interface Result {
  id: string;
  template: string;
  ok: boolean;
  reason?: string;
  samples: number;
  steps: number;
}

const CHECK_PY = `
import json, sys
from pathlib import Path
from lla_eval.scoring import run_check
a = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
res = [run_check(c, a["out"], Path(a["set_dir"])) for c in a["checks"]]
print(json.dumps([{"ok": ok, "detail": d} for ok, d in res], ensure_ascii=False))
`;

export async function runEpisode(ep: Episode, work: string, samplesOut: string[]): Promise<Result> {
  const dir = join(work, ep.id);
  const ws = join(dir, "ws");
  const outside = join(dir, "outside");
  const appData = join(dir, "appdata");
  for (const d of [ws, outside, appData]) mkdirSync(d, { recursive: true });
  for (const [rel, text] of Object.entries(ep.files)) {
    mkdirSync(dirname(join(ws, rel)), { recursive: true });
    writeFileSync(join(ws, rel), text);
  }
  for (const [rel, text] of Object.entries(ep.expected)) writeFileSync(join(dir, rel), text);

  // モックのシナリオ: agent の n 番目の推論に n 番目の手を返す。請求書の抽出は決め打ちの正解
  const steps: unknown[] = ep.actions.map((a, i) => ({ match: { step_index: i }, latency_ms: 0, tokens_per_sec: 5000, action: a }));
  if (ep.invoices) {
    const broken = ep.invoices.broken ? JSON.stringify(ep.invoices.broken) : "";
    const gen = await uvPython([
      "-m",
      "lla_eval",
      "gen-invoices",
      join(ws, ep.invoices.dir),
      "--count",
      String(ep.invoices.count),
      "--no-expected",
      "--mock-fixtures",
      join(dir, "fixtures", "invoice"),
      ...(broken ? ["--broken", broken] : []),
    ]);
    if (gen.code !== 0) return { id: ep.id, template: ep.template, ok: false, reason: `請求書を作れません: ${gen.stderr.slice(-200)}`, samples: 0, steps: 0 };
    steps.push({ match: { purpose: "invoice_extract" }, repeat: true, latency_ms: 0, action_from_fixture: "fixtures/invoice/{item_basename}.json" });
  }
  const scenario = join(dir, "scenario.yaml");
  writeFileSync(
    scenario,
    JSON.stringify({ name: `datagen-${ep.id}`, config: { context_budget: 32768, models: ["local-12b"] }, steps, fallback: { action: { type: "final", summary: "（想定外の手）", outputs: [], failures: [], needs_review: [] } } }),
  );

  const samplesFile = join(dir, "samples.jsonl");
  const mock = new MockGatewayProc(scenario, join(dir, "mock"), { pingIntervalS: 0.3, samples: samplesFile });
  await mock.start();
  const events: RuntimeEvent[] = [];
  const h = createHeadless({ gatewayUrl: mock.url, login: "dev", password: "dev", appDataDir: appData, dbKey: "datagen", office: true, emit: (e) => events.push(e), runtime: { retryBaseMs: 200 } });
  let runId = "";
  let run: ReturnType<typeof h.store.getRun>;
  let stepCount = 0;
  try {
    await h.runtime.start();
    runId = ((await h.runtime.handle(cmd("run.start", { conversation_id: ep.id, text: ep.request, attachments: [], workspace: ws, model: "auto" }))) as { run_id: string }).run_id;
    const deadline = Date.now() + 180_000;
    while (!h.store.getRun(runId)?.ended_at) {
      if (Date.now() > deadline) {
        await h.runtime.handle(cmd("run.stop", { run_id: runId }));
        break;
      }
      await sleep(100);
    }
  } finally {
    run = runId ? h.store.getRun(runId) : undefined;
    stepCount = runId ? (h.store.db.prepare("SELECT COUNT(*) n FROM steps WHERE run_id = ?").get(runId) as { n: number }).n : 0;
    await h.close();
    await mock.stop();
  }
  const final = run?.final_action ? (JSON.parse(run.final_action) as unknown) : null;
  if (!run || run.end_reason !== "final") {
    return { id: ep.id, template: ep.template, ok: false, reason: `完了しませんでした（${run?.end_reason ?? "?"}）`, samples: 0, steps: stepCount };
  }
  // 成果物の確認（評価ハーネスと同じ確認方法）
  const arg = join(dir, "check.json");
  writeFileSync(arg, JSON.stringify({ checks: ep.checks, set_dir: dir, out: { workspace: ws, outside_files: [], final, steps: stepCount } }));
  const r = await uvPython(["-c", CHECK_PY, arg]);
  const line = r.stdout.trim().split(/\r?\n/).pop() ?? "[]";
  const results = JSON.parse(line.startsWith("[") ? line : "[]") as { ok: boolean; detail: string }[];
  const failed = results.filter((x) => !x.ok);
  if (results.length !== ep.checks.length || failed.length) {
    return { id: ep.id, template: ep.template, ok: false, reason: failed.map((f) => f.detail).join(" / ") || r.stderr.slice(-200), samples: 0, steps: stepCount };
  }
  // 合格: Runtime が組み立てた入力と、正しい手の組を学習データにする（抽出推論の組は使わない）
  const recs = existsSync(samplesFile)
    ? readFileSync(samplesFile, "utf8")
        .split("\n")
        .filter(Boolean)
        .map((l) => JSON.parse(l) as { purpose: string; messages: unknown[]; output: string; constrained: boolean; index: number })
    : [];
  const agent = recs.filter((x) => x.purpose === "agent");
  for (const x of agent) {
    samplesOut.push(JSON.stringify({ episode: ep.id, template: ep.template, step: x.index, messages: x.messages, completion: x.output, constrained: x.constrained }));
  }
  return { id: ep.id, template: ep.template, ok: true, samples: agent.length, steps: stepCount };
}

