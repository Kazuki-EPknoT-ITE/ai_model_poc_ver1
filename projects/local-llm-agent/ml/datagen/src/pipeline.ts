// 手順例1件の実行と検証（gen.ts と試験から使う）
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
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
  /** 作業フォルダに置いた入力ファイルの sha256（評価データとの重複照合用。test_spec §3.1） */
  inputHashes?: string[];
}

const CHECK_PY = `
import json, sys
from pathlib import Path
from lla_eval.scoring import run_check
a = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
res = [run_check(c, a["out"], Path(a["set_dir"])) for c in a["checks"]]
print(json.dumps([{"ok": ok, "detail": d} for ok, d in res], ensure_ascii=False))
`;

const OFFICE_PY = `
import json, sys
from pathlib import Path
import docx
from openpyxl import Workbook
a = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for rel, f in a["docx"].items():
    d = docx.Document()
    d.add_heading(f["heading"], level=1)
    for t in f["paragraphs"]:
        d.add_paragraph(t)
    p = Path(a["ws"]) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    d.save(p)
for rel, f in a["xlsx"].items():
    wb = Workbook()
    ws = wb.active
    ws.title = f["sheet"]
    for row in f["rows"]:
        ws.append(row)
    p = Path(a["ws"]) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    wb.save(p)
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
  if (ep.xlsx || ep.docx) {
    const spec = join(dir, "office.json");
    writeFileSync(spec, JSON.stringify({ ws, xlsx: ep.xlsx ?? {}, docx: ep.docx ?? {} }));
    const x = await uvPython(["-c", OFFICE_PY, spec]);
    if (x.code !== 0) return { id: ep.id, template: ep.template, ok: false, reason: `Excel・Word を作れません: ${x.stderr.slice(-200)}`, samples: 0, steps: 0 };
  }

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
      "--start",
      String(ep.invoices.start),
      "--no-expected",
      "--mock-fixtures",
      join(dir, "fixtures", "invoice"),
      ...(broken ? ["--broken", broken] : []),
    ]);
    if (gen.code !== 0) return { id: ep.id, template: ep.template, ok: false, reason: `請求書を作れません: ${gen.stderr.slice(-200)}`, samples: 0, steps: 0 };
    steps.push({ match: { purpose: "invoice_extract" }, repeat: true, latency_ms: 0, action_from_fixture: "fixtures/invoice/{item_basename}.json" });
  }
  const inputHashes = listFiles(ws).map((f) => createHash("sha256").update(readFileSync(f)).digest("hex"));
  const scenario = join(dir, "scenario.yaml");
  writeFileSync(
    scenario,
    JSON.stringify({ name: `datagen-${ep.id}`, config: { context_budget: 32768, models: ["local-12b"], ...(ep.features ? { features: ep.features } : {}) }, ...(ep.fixtures ? { fixtures: ep.fixtures } : {}), steps, fallback: { action: { type: "final", summary: "（想定外の手）", outputs: [], failures: [], needs_review: [] } } }),
  );

  const samplesFile = join(dir, "samples.jsonl");
  const mock = new MockGatewayProc(scenario, join(dir, "mock"), { pingIntervalS: 0.3, samples: samplesFile });
  await mock.start();
  const events: RuntimeEvent[] = [];
  const pending: string[] = [];
  const questions: string[] = [];
  // 承認の判断は、モデルが待ちの完了を出して承認待ちになってから返す（手の順番を決まったものにするため）
  const emit = (e: RuntimeEvent): void => {
    events.push(e);
    if (e.type === "approval.request") pending.push((e.payload as { approval_id: string }).approval_id);
    if (e.type === "question") questions.push((e.payload as { question_id: string }).question_id);
    if (e.type !== "run.state") return;
    const state = (e.payload as { state: string }).state;
    if (ep.approval && state === "waiting_approval") {
      for (const id of pending.splice(0)) void h.runtime.handle(cmd("approval.decide", { approval_id: id, decision: ep.approval })).catch(() => undefined);
    }
    if (ep.answer !== undefined && state === "waiting_question") {
      for (const id of questions.splice(0)) void h.runtime.handle(cmd("question.answer", { question_id: id, answer: ep.answer })).catch(() => undefined);
    }
  };
  const h = createHeadless({ gatewayUrl: mock.url, login: "dev", password: "dev", appDataDir: appData, dbKey: "datagen", office: true, emit, runtime: { retryBaseMs: 200 } });
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
  return { id: ep.id, template: ep.template, ok: true, samples: agent.length, steps: stepCount, inputHashes };
}


function listFiles(d: string): string[] {
  return readdirSync(d, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? listFiles(join(d, e.name)) : [join(d, e.name)]));
}
