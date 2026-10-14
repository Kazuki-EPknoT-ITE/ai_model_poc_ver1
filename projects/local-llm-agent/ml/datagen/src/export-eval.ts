// 雛形から、ツール操作の評価セット（eval/sets/<name>/、test_spec §3 の tool_tasks 形式）を書き出す。
//   pnpm --filter @lla/datagen export-eval -- [--per-template 5] [--seed 1] [--name tool_tasks_synth] [--out <フォルダ>]
//
// 目的: 検証機でベースモデルと学習後のモデルを同じ物差しで比べる（POC 条件2）。実データの評価セット（PHASE 0）が
// そろうまでの代わり。学習と同じ雛形なので、学習で上がりやすい（分布の内側の評価）ことに注意して読む。
// - 種は学習データの生成（gen.ts: seed×100000＋…）と重ならない範囲（EVAL_SEED_BASE から）を使う。
// - 請求書の中身の通し番号も、学習（10000〜99999）と重ならない範囲（100000〜）にする。
// - 作業フォルダ・期待値・モックのシナリオを書き、items.jsonl と manifest.yaml（data_hash つき）を作る。
//   承認・質問のある手順例は、items の approval / answer で評価ハーネスが利用者の役をする。
// - 社内文書の検索（rag_answer）は、実モデルでは RAG サーバーが要るため入れない。
// 書き出したら `pnpm eval --set <name>`（モック）で全件が合格することを確かめる。実モデルは `--gateway` を付ける。
import { rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { llaEval } from "../../../eval/harness/src/env.js";
import { materialize } from "./pipeline.js";
import { type Episode, TEMPLATES, rng } from "./templates.js";

const DATAGEN_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const SETS_DIR = resolve(DATAGEN_DIR, "..", "..", "eval", "sets");
export const EVAL_SEED_BASE = 900_000_000;
const EVAL_INVOICE_START = 100_000;
const EXCLUDE = new Set(["rag_answer"]);

function parse(argv: string[]) {
  const o = { perTemplate: 5, seed: 1, name: "tool_tasks_synth", out: "" };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]!;
    if (a === "--") continue;
    else if (a === "--per-template") o.perTemplate = Number(argv[++i]);
    else if (a === "--seed") o.seed = Number(argv[++i]);
    else if (a === "--name") o.name = argv[++i]!;
    else if (a === "--out") o.out = resolve(argv[++i]!);
    else throw new Error(`不明な引数: ${a}`);
  }
  if (!/^[a-z0-9_]+$/.test(o.name)) throw new Error("--name は英小文字・数字・_ で指定してください");
  return { ...o, out: o.out || join(SETS_DIR, o.name) };
}

/** 請求書の通し番号を評価用の範囲に移し、期待値（請求元の番号と金額）も合わせる */
function shiftInvoices(ep: Episode, start: number): void {
  if (!ep.invoices) return;
  const delta = start - ep.invoices.start;
  ep.invoices = { ...ep.invoices, start };
  for (const [rel, text] of Object.entries(ep.expected)) {
    if (!rel.endsWith(".json")) continue;
    const exp = JSON.parse(text) as { rows: (string | number)[][] };
    exp.rows = exp.rows.map((row, i) => {
      if (i === 0) return row;
      const n = Number(/(\d+)$/.exec(String(row[0]))![1]) + delta;
      return [String(row[0]).replace(/\d+$/, String(n)), n * 1000];
    });
    ep.expected[rel] = JSON.stringify(exp);
  }
}

async function main(): Promise<number> {
  const o = parse(process.argv.slice(2));
  rmSync(o.out, { recursive: true, force: true });
  const names = Object.keys(TEMPLATES).filter((n) => !EXCLUDE.has(n));
  const items: Record<string, unknown>[] = [];
  let k = 0;
  for (const name of names) {
    for (let i = 0; i < o.perTemplate; i++, k++) {
      const seed = EVAL_SEED_BASE + o.seed * 100_000 + Object.keys(TEMPLATES).indexOf(name) * 1000 + i;
      const id = `${name}-${String(i + 1).padStart(2, "0")}`;
      const ep = TEMPLATES[name]!(rng(seed), id);
      shiftInvoices(ep, EVAL_INVOICE_START + k * 100);
      const m = await materialize(ep, join(o.out, "fixtures", "workspaces", id), join(o.out, "expected", id), join(o.out, "mock", id));
      if ("error" in m) throw new Error(`${id}: ${m.error}`);
      const checks = ep.checks.map((c) => (typeof c.expected === "string" ? { ...c, expected: `expected/${id}/${c.expected}` } : c));
      const toolCalls = ep.actions.filter((a) => a.type === "tool_call").length;
      items.push({
        id,
        template: name,
        request: ep.request,
        workspace_fixture: `workspaces/${id}`,
        net_level: "off",
        mock: `mock/${id}/scenario.yaml`,
        ...(ep.approval ? { approval: ep.approval } : {}),
        ...(ep.answer !== undefined ? { answer: ep.answer } : {}),
        // 手順数の上限は、正しい手順の4倍＋10（毎手のあとに計画を出し直すモデルでも通り、堂々巡りは不合格にする）
        checks: [...checks, { type: "max_steps", value: toolCalls * 4 + 10 }],
      });
      process.stderr.write(`${id}\n`);
    }
  }
  writeFileSync(join(o.out, "items.jsonl"), items.map((x) => JSON.stringify(x)).join("\n") + "\n");
  const manifest = (dataHash: string) =>
    [
      `# 雛形（ml/datagen）から書き出したツール操作の評価セット。書き出し: pnpm --filter @lla/datagen export-eval -- --per-template ${o.perTemplate} --seed ${o.seed} --name ${o.name}`,
      "# 学習と同じ雛形なので、分布の内側の評価になる。実データの評価セット（PHASE 0）がそろうまでの、ベースモデルと学習後の比較用。",
      "# --gateway 省略時は item ごとのモック（mock/<id>/scenario.yaml）が正しい手順を返す（セットとハーネスの確認用）。",
      `name: ${o.name}`,
      "version: 1",
      `description: 雛形 ${names.length} 種 × ${o.perTemplate} 件（承認・質問・回復・プロンプトインジェクションを含む）`,
      'requirement_ids: [AGT-01, AGT-02, AGT-03, XLS-01, DOC-01, "9.1 ツール操作", "9.2-2"]',
      "data_root: fixtures",
      `data_hash: "${dataHash}"`,
      "split: eval",
      "runner: agent",
      "scorer: artifact_checks",
      "model_targets: [fast]",
      "metrics:",
      '  - { name: task_success_rate, target: ">= 0.8" }',
      "timeout_per_item_s: 600",
      "repeat: 1",
      `seed: ${o.seed}`,
      "temperature: 0",
      "",
    ].join("\n");
  writeFileSync(join(o.out, "manifest.yaml"), manifest(`sha256:${"0".repeat(64)}`));
  const h = await llaEval<{ data_hash: string }>(["hash", o.out, "--cache", join(o.out, ".cache")]);
  rmSync(join(o.out, ".cache"), { recursive: true, force: true });
  writeFileSync(join(o.out, "manifest.yaml"), manifest(h.data_hash));
  process.stderr.write(`\n${items.length} 件 → ${o.out}（data_hash ${h.data_hash}）\n`);
  return 0;
}

main().then(
  (code) => process.exit(code),
  (e: unknown) => {
    process.stderr.write(`${(e as Error).stack ?? String(e)}\n`);
    process.exit(2);
  },
);
