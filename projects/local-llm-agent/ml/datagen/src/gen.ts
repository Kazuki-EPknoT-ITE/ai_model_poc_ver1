// ツール操作の手順例の合成と検証（T-52、設計書 §8.3）。
//   pnpm --filter @lla/datagen gen -- --count 5 [--seed 1] [--only memo_to_csv,docx_report] [--out <フォルダ>] [--jobs 4]
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
import { checkEvalOverlap } from "./overlap.js";
import { type Result, runEpisode } from "./pipeline.js";
import { TEMPLATES, rng } from "./templates.js";

const DATAGEN_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function parse(argv: string[]) {
  const o = { count: 3, seed: 1, only: [] as string[], out: join(DATAGEN_DIR, "out", new Date().toISOString().slice(0, 10)), keep: false, jobs: 1 };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]!;
    if (a === "--") continue;
    else if (a === "--count") o.count = Number(argv[++i]);
    else if (a === "--seed") o.seed = Number(argv[++i]);
    else if (a === "--only") o.only = argv[++i]!.split(",");
    else if (a === "--out") o.out = resolve(argv[++i]!);
    else if (a === "--keep") o.keep = true;
    else if (a === "--jobs") o.jobs = Number(argv[++i]);
    else throw new Error(`不明な引数: ${a}`);
  }
  if (!Number.isInteger(o.jobs) || o.jobs < 1 || o.jobs > 32) throw new Error("--jobs は 1〜32 で指定してください");
  return o;
}

async function main(): Promise<number> {
  const o = parse(process.argv.slice(2));
  const names = Object.keys(TEMPLATES).filter((n) => !o.only.length || o.only.includes(n));
  const work = mkdtempSync(join(tmpdir(), "lla-datagen-"));
  // 種は雛形の並び（全体）で決める。--only で絞っても同じ手順例になる
  const tasks = names.flatMap((name) => Array.from({ length: o.count }, (_, i) => ({ name, seed: o.seed * 100_000 + Object.keys(TEMPLATES).indexOf(name) * 1000 + i })));
  // 手順例ごとにフォルダ・モック・Runtime が別なので、並行して実行できる（--jobs）。
  // 出力は終わった順ではなく tasks の順に並べる（--jobs を変えても、手順例の並びと中身の手は同じ）
  const perTask: string[][] = tasks.map(() => []);
  const results: Result[] = new Array<Result>(tasks.length);
  let next = 0;
  const started = Date.now();
  const worker = async (): Promise<void> => {
    while (next < tasks.length) {
      const k = next++;
      const { name, seed } = tasks[k]!;
      const ep = TEMPLATES[name]!(rng(seed), `${name}-${seed}`);
      const res = await runEpisode(ep, work, perTask[k]!);
      results[k] = res;
      process.stderr.write(`${res.ok ? "合格" : "不合格"} ${res.id}（手順 ${res.steps}、学習データ ${res.samples} 件）${res.reason ? ` ${res.reason}` : ""}\n`);
    }
  };
  await Promise.all(Array.from({ length: Math.min(o.jobs, tasks.length) }, worker));
  const samples = perTask.flat();
  mkdirSync(o.out, { recursive: true });
  const body = samples.join("\n") + (samples.length ? "\n" : "");
  writeFileSync(join(o.out, "train.jsonl"), body);
  const manifest = {
    created_at: new Date().toISOString(),
    seed: o.seed,
    jobs: o.jobs,
    elapsed_s: Math.round((Date.now() - started) / 1000),
    // 学習データの前提（雛形・形式が変わったら作り直す。ADR-002、local_llm_prompts.md）
    protocol_version: PROTOCOL_VERSION,
    prompt_version: PROMPT_VERSION,
    episodes: results.length,
    passed: results.filter((r) => r.ok).length,
    samples: samples.length,
    by_template: Object.fromEntries(names.map((n) => [n, { passed: results.filter((r) => r.template === n && r.ok).length, total: results.filter((r) => r.template === n).length }])),
    failures: results.filter((r) => !r.ok).map((r) => ({ id: r.id, reason: r.reason })),
    // 出所の照合用（lla_train prepare が確かめ、stats.json の sources に残す。台帳の train_data_hash は prepare の data_hash）
    sha256: createHash("sha256").update(body).digest("hex"),
  };
  // 学習に使った入力ファイルのハッシュ一覧。評価セットの manifest の train_hash_index（または LLA_EVAL_TRAIN_HASHES）に指定すると、
  // 評価データと重複していれば評価が実行されない（test_spec §3.1）
  const hashes = [...new Set(results.filter((r) => r.ok).flatMap((r) => r.inputHashes ?? []))].sort();
  writeFileSync(
    join(o.out, "train_hashes.txt"),
    `# ml/datagen の入力ファイルの sha256（${manifest.created_at}、seed ${o.seed}）\n` + hashes.map((h) => `sha256:${h}\n`).join(""),
  );
  // 評価セットと同じファイルを学習に使っていないか（使っていれば、このデータは使えない）
  const overlap = await checkEvalOverlap(hashes);
  writeFileSync(
    join(o.out, "manifest.json"),
    JSON.stringify({ ...manifest, input_files: hashes.length, eval_overlap: { checked: overlap.checked, skipped: overlap.skipped, files: overlap.overlap } }, null, 1),
  );
  if (!o.keep) rmSync(work, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  process.stderr.write(`\n${manifest.passed}/${manifest.episodes} 件合格、学習データ ${manifest.samples} 件 → ${o.out}\n`);
  if (overlap.skipped.length) process.stderr.write(`評価セットとの照合をしていません（データが手元に無い）: ${overlap.skipped.join("、")}\n`);
  if (overlap.overlap.length) {
    process.stderr.write(`評価セットと同じファイルを学習データに使っています（${overlap.overlap.length}件）: ${overlap.overlap.slice(0, 5).join("、")}。このデータは使えません\n`);
    return 3;
  }
  return manifest.passed === manifest.episodes ? 0 : 1;
}

main().then(
  (code) => process.exit(code),
  (e: unknown) => {
    process.stderr.write(`${(e as Error).stack ?? String(e)}\n`);
    process.exit(2);
  },
);
