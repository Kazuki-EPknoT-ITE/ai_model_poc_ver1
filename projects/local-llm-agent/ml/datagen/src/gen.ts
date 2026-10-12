// ツール操作の手順例の合成と検証（T-52、設計書 §8.3）。
//   pnpm --filter @lla/datagen gen -- --count 5 [--seed 1] [--only memo_to_csv,docx_report] [--out <フォルダ>]
//
// 1. 雛形（templates.ts）から手順例（依頼・ファイル・正しい手順・確認方法）を作る。
// 2. 手順をモック Gateway のシナリオにし、本物の Runtime（ヘッドレス）と office-tools で最後まで実行する。
//    モックは、Runtime が組み立てた推論の入力（messages）と返した出力の組を記録する（--record-samples）。
// 3. 成果物を評価ハーネスの確認（lla_eval.scoring.run_check）で確かめ、合格した手順例の組だけを学習データにする。
// 出力: <out>/train.jsonl（1行1手: messages と completion）、<out>/manifest.json（件数・合否・sha256）。
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { PROMPT_VERSION } from "@lla/agent-runtime";
import { PROTOCOL_VERSION } from "@lla/protocol";
import { type Result, runEpisode } from "./pipeline.js";
import { TEMPLATES, rng } from "./templates.js";

const DATAGEN_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function parse(argv: string[]) {
  const o = { count: 3, seed: 1, only: [] as string[], out: join(DATAGEN_DIR, "out", new Date().toISOString().slice(0, 10)), keep: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]!;
    if (a === "--") continue;
    else if (a === "--count") o.count = Number(argv[++i]);
    else if (a === "--seed") o.seed = Number(argv[++i]);
    else if (a === "--only") o.only = argv[++i]!.split(",");
    else if (a === "--out") o.out = resolve(argv[++i]!);
    else if (a === "--keep") o.keep = true;
    else throw new Error(`不明な引数: ${a}`);
  }
  return o;
}

async function main(): Promise<number> {
  const o = parse(process.argv.slice(2));
  const names = Object.keys(TEMPLATES).filter((n) => !o.only.length || o.only.includes(n));
  const work = mkdtempSync(join(tmpdir(), "lla-datagen-"));
  const samples: string[] = [];
  const results: Result[] = [];
  for (const name of names) {
    for (let i = 0; i < o.count; i++) {
      const seed = o.seed * 100_000 + names.indexOf(name) * 1000 + i;
      const ep = TEMPLATES[name]!(rng(seed), `${name}-${seed}`);
      const res = await runEpisode(ep, work, samples);
      results.push(res);
      process.stderr.write(`${res.ok ? "合格" : "不合格"} ${res.id}（手順 ${res.steps}、学習データ ${res.samples} 件）${res.reason ? ` ${res.reason}` : ""}\n`);
    }
  }
  mkdirSync(o.out, { recursive: true });
  const body = samples.join("\n") + (samples.length ? "\n" : "");
  writeFileSync(join(o.out, "train.jsonl"), body);
  const manifest = {
    created_at: new Date().toISOString(),
    seed: o.seed,
    // 学習データの前提（雛形・形式が変わったら作り直す。ADR-002、local_llm_prompts.md）
    protocol_version: PROTOCOL_VERSION,
    prompt_version: PROMPT_VERSION,
    episodes: results.length,
    passed: results.filter((r) => r.ok).length,
    samples: samples.length,
    by_template: Object.fromEntries(names.map((n) => [n, { passed: results.filter((r) => r.template === n && r.ok).length, total: results.filter((r) => r.template === n).length }])),
    failures: results.filter((r) => !r.ok).map((r) => ({ id: r.id, reason: r.reason })),
    // モデル台帳の train_data_hash に登録する値
    sha256: createHash("sha256").update(body).digest("hex"),
  };
  writeFileSync(join(o.out, "manifest.json"), JSON.stringify(manifest, null, 1));
  if (!o.keep) rmSync(work, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  process.stderr.write(`\n${manifest.passed}/${manifest.episodes} 件合格、学習データ ${manifest.samples} 件 → ${o.out}\n`);
  return manifest.passed === manifest.episodes ? 0 : 1;
}

main().then(
  (code) => process.exit(code),
  (e: unknown) => {
    process.stderr.write(`${(e as Error).stack ?? String(e)}\n`);
    process.exit(2);
  },
);
